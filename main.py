import os
import email
from email.policy import default
import email.utils
import subprocess
import tempfile
import time
import logging
import argparse
import ssl
import re
import signal

import urllib.parse
import urllib.request
import threading

from dotenv import load_dotenv
from imapclient import IMAPClient
import sentry_sdk

shutdown_event = threading.Event()

def handle_shutdown_signal(signum, frame):
    sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
    logging.info(f"Received shutdown signal ({sig_name}). Shutting down gracefully...")
    shutdown_event.set()

signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)

try:
    from pocketbase import PocketBase
    import httpx
    HAS_POCKETBASE = True
except ImportError:
    HAS_POCKETBASE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

SENTRY_DSN = os.getenv('SENTRY_DSN')
try:
    SENTRY_TRACES_SAMPLE_RATE = float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.1'))
except ValueError:
    SENTRY_TRACES_SAMPLE_RATE = 0.1

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
    )
    logging.info(f"Sentry integration initialized (traces_sample_rate={SENTRY_TRACES_SAMPLE_RATE}).")

UPTIME_KUMA_PUSH_URL = os.getenv('UPTIME_KUMA_PUSH_URL')
try:
    HEARTBEAT_INTERVAL = int(os.getenv('HEARTBEAT_INTERVAL', '60'))
except ValueError:
    HEARTBEAT_INTERVAL = 60

HOST = os.getenv('IMAP_HOST')
USERNAME = os.getenv('IMAP_USERNAME')
PASSWORD = os.getenv('IMAP_PASSWORD')
try:
    PAGE_LIMIT = max(1, int(os.getenv('PAGE_LIMIT', '5')))
except ValueError:
    PAGE_LIMIT = 5
PRINTER_NAME = os.getenv('PRINTER_NAME', '')

try:
    MAX_ATTACHMENT_SIZE = int(os.getenv('MAX_ATTACHMENT_SIZE', str(6 * 1024 * 1024)))
except ValueError:
    MAX_ATTACHMENT_SIZE = 6 * 1024 * 1024

ALLOWED_EXTENSIONS_RAW = os.getenv('ALLOWED_EXTENSIONS', '.pdf,.jpg,.jpeg,.png,.txt,.doc,.docx')
ALLOWED_EXTENSIONS = {
    ext.strip().lower() if ext.strip().startswith('.') else f".{ext.strip().lower()}"
    for ext in ALLOWED_EXTENSIONS_RAW.split(',')
    if ext.strip()
}

ALLOWED_SENDERS_RAW = os.getenv('ALLOWED_SENDERS', '')
ALLOWED_SENDERS = [s.strip().lower() for s in ALLOWED_SENDERS_RAW.split(',') if s.strip()]

PB_URL = os.getenv('POCKETBASE_URL')
PB_USER = os.getenv('POCKETBASE_USER')
PB_PASSWORD = os.getenv('POCKETBASE_PASSWORD')

service_state_lock = threading.Lock()
service_state = {
    'status': 'starting',
    'msg': 'Service starting',
}

def set_service_state(status: str, msg: str):
    with service_state_lock:
        service_state['status'] = status
        service_state['msg'] = msg

def get_service_state():
    with service_state_lock:
        return service_state.get('status', 'up'), service_state.get('msg', 'OK')

def sanitize_filename(filename: str) -> str:
    base = os.path.basename(filename or 'document.pdf')
    safe = re.sub(r'[^\w.\-]', '_', base)
    return safe if safe else 'document.pdf'

def is_allowed_file(filename: str) -> bool:
    if not filename:
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS

def is_sender_allowed(sender_header: str) -> bool:
    if not ALLOWED_SENDERS:
        return True
    _, sender_email = email.utils.parseaddr(sender_header)
    sender_email = sender_email.lower().strip()
    if not sender_email:
        return False
    for allowed in ALLOWED_SENDERS:
        if allowed.startswith('@'):
            if sender_email.endswith(allowed):
                return True
        elif '@' not in allowed:
            if sender_email.endswith(f"@{allowed}") or sender_email == allowed:
                return True
        else:
            if sender_email == allowed:
                return True
    return False

def send_heartbeat(push_url, status='up', msg='OK'):
    try:
        parsed = urllib.parse.urlparse(push_url)
        if parsed.scheme.lower() != 'https':
            logging.warning(f"Heartbeat URL must use HTTPS (got '{parsed.scheme}://'). Skipping heartbeat.")
            return

        # Uptime Kuma push monitors only recognize 'up' or 'down'
        kuma_status = 'down' if status == 'down' else 'up'

        query_params = urllib.parse.parse_qs(parsed.query)
        query_params['status'] = [kuma_status]
        query_params['msg'] = [msg]

        new_query = urllib.parse.urlencode(query_params, doseq=True)
        target_url = urllib.parse.urlunparse(parsed._replace(query=new_query))

        req = urllib.request.Request(
            target_url,
            headers={'User-Agent': 'emailtoprint-heartbeat/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
        logging.debug(f"Heartbeat sent successfully (status={kuma_status}, msg={msg})")
    except Exception as e:
        logging.warning(f"Failed to send heartbeat to Uptime Kuma: {e}")

def start_heartbeat_worker(push_url, interval):
    def _worker():
        logging.info(f"Uptime Kuma heartbeat worker started (interval: {interval}s).")
        while not shutdown_event.is_set():
            start_ts = time.time()
            status, msg = get_service_state()
            send_heartbeat(push_url, status=status, msg=msg)
            elapsed = time.time() - start_ts
            sleep_time = max(1.0, float(interval) - elapsed)
            for _ in range(int(sleep_time)):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

def validate_file_content(filepath: str, filename: str) -> tuple[bool, str]:
    """
    Validates that a file is readable, not empty, and matches expected magic bytes
    for its file extension (e.g. valid %PDF- header for .pdf files).
    """
    if not os.path.isfile(filepath):
        return False, f"File '{filename}' does not exist or is not a regular file."

    try:
        size = os.path.getsize(filepath)
    except OSError as e:
        return False, f"Could not read size of file '{filename}': {e}"

    if size == 0:
        return False, f"File '{filename}' is empty (0 bytes)."

    ext = os.path.splitext(filename)[1].lower()

    try:
        with open(filepath, 'rb') as f:
            header = f.read(1024)
    except OSError as e:
        return False, f"Could not open file '{filename}' for reading: {e}"

    if ext == '.pdf':
        if b'%PDF-' not in header:
            return False, f"File '{filename}' has a .pdf extension but lacks a valid '%PDF-' header."
    elif ext in ('.jpg', '.jpeg'):
        if len(header) < 3 or header[:3] != b'\xff\xd8\xff':
            return False, f"File '{filename}' has a JPEG extension but lacks valid JPEG magic bytes."
    elif ext == '.png':
        if len(header) < 8 or header[:8] != b'\x89PNG\r\n\x1a\n':
            return False, f"File '{filename}' has a .png extension but lacks valid PNG magic bytes."
    elif ext == '.txt':
        if b'\x00' in header:
            return False, f"File '{filename}' has a .txt extension but contains null bytes (binary content)."
    elif ext == '.docx':
        if len(header) < 4 or header[:4] != b'PK\x03\x04':
            return False, f"File '{filename}' has a .docx extension but lacks valid ZIP magic bytes."
    elif ext == '.doc':
        if len(header) < 8 or header[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
            return False, f"File '{filename}' has a .doc extension but lacks valid OLE compound magic bytes."

    return True, ""

def get_cups_printer_status(printer_name: str = '') -> tuple[bool, str, str]:
    """
    Queries lpstat -p for printer state.
    Returns: (is_enabled, status_line, alert_message)
    """
    cmd = ['lpstat', '-p']
    if printer_name:
        cmd.append(printer_name)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        output = res.stdout.strip()
        lines = [line.strip() for line in output.split('\n') if line.strip()]
        if not lines:
            return True, "", ""

        status_line = lines[0]
        alert_msg = ""
        if len(lines) > 1:
            alert_msg = " | ".join(lines[1:])

        is_disabled = "disabled since" in status_line.lower() or "désactivée" in status_line.lower()
        return not is_disabled, status_line, alert_msg
    except Exception as e:
        return True, "", f"Could not query printer status: {e}"

def print_file(filepath, filename, paper_size='letter'):
    # Pre-flight: Validate file content and magic bytes
    valid, err_msg = validate_file_content(filepath, filename)
    if not valid:
        logging.error(f"Pre-print validation failed for {filename}: {err_msg}")
        return False, err_msg

    # Pre-flight: Check printer status
    is_enabled, status_line, alert_msg = get_cups_printer_status(PRINTER_NAME)
    if not is_enabled:
        logging.warning(f"Printer '{PRINTER_NAME or 'default'}' is currently disabled in CUPS: {status_line}")
    if alert_msg:
        logging.warning(f"Printer '{PRINTER_NAME or 'default'}' status alert before printing: {alert_msg}")

    media_name = 'Legal' if str(paper_size).strip().lower() == 'legal' else 'Letter'
    cmd = ['lp']
    if PRINTER_NAME:
        cmd.extend(['-d', PRINTER_NAME])
    cmd.extend([
        '-o', f'media={media_name}',
        '-o', 'sides=two-sided-long-edge',
        '-o', f'page-ranges=1-{PAGE_LIMIT}',
        filepath
    ])
    try:
        logging.info(f"Printing {filename} on {media_name} (up to {PAGE_LIMIT} pages)...")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        out = result.stdout.strip()
        logging.info(f"Print job queued successfully: {out}")

        # Extract CUPS Job ID (e.g. "request id is HP_LaserJet_Flow_MFP_M630-38 (1 file(s))")
        job_id = ""
        m = re.search(r'request id is\s+([^\s]+)', out, re.IGNORECASE)
        if m:
            job_id = m.group(1)
        else:
            m = re.search(r'([A-Za-z0-9_.-]+-\d+)', out)
            if m:
                job_id = m.group(1)

        if job_id:
            dest = PRINTER_NAME or job_id.rsplit('-', 1)[0]
            # Give CUPS filter pipeline a brief window (2s) to attempt rendering
            time.sleep(2)

            _, _, post_alert = get_cups_printer_status(dest)
            filter_error_keywords = [
                "can’t open the file", "can't open the file",
                "filter failed", "stopped with status",
                "does not contain any pages", "unable to open"
            ]
            has_filter_error = any(kw in post_alert.lower() for kw in filter_error_keywords)

            if has_filter_error:
                err_detail = f"CUPS filter failure detected for {filename} ({job_id}): {post_alert}"
                logging.error(err_detail)
                # Attempt to cancel the stalled failed job so it doesn't block the queue
                try:
                    subprocess.run(['cancel', job_id], capture_output=True, check=False)
                    logging.info(f"Cancelled stalled failed job {job_id} from queue.")
                except Exception as cancel_err:
                    logging.warning(f"Failed to cancel job {job_id}: {cancel_err}")
                return False, err_detail

            if "media-empty" in post_alert.lower():
                logging.warning(f"Printer '{dest}' reports paper tray empty ({post_alert}). Job {job_id} is waiting for paper.")
            elif post_alert:
                logging.warning(f"Printer '{dest}' reported status alert for job {job_id}: {post_alert}")

        return True, out
    except subprocess.CalledProcessError as e:
        err = e.stderr.strip() if e.stderr else str(e)
        logging.error(f"Failed to print {filename}. Error: {err}")
        return False, err
    except FileNotFoundError:
        err = "The 'lp' command was not found. Is CUPS installed?"
        logging.error(err)
        return False, err

def process_pocketbase_record(pb: "PocketBase", record):
    rec_id = getattr(record, 'id', '') or (record.get('id') if isinstance(record, dict) else '')
    status = getattr(record, 'status', '') or (record.get('status') if isinstance(record, dict) else '')
    raw_filename = getattr(record, 'filename', '') or (record.get('filename') if isinstance(record, dict) else '')
    file_field = getattr(record, 'file', '') or (record.get('file') if isinstance(record, dict) else '')
    raw_paper_size = getattr(record, 'paper_size', '') or (record.get('paper_size') if isinstance(record, dict) else '')
    paper_size = str(raw_paper_size).strip().lower() if raw_paper_size else 'letter'
    if paper_size not in ('letter', 'legal'):
        paper_size = 'letter'

    if status != 'queued' or not file_field:
        return

    # Resolve filename accurately from record.filename and/or file_field
    if not raw_filename:
        filename = file_field or 'document.txt'
    else:
        # If raw_filename has no extension, inherit extension from file_field if present
        _, raw_ext = os.path.splitext(raw_filename)
        _, field_ext = os.path.splitext(file_field)
        if not raw_ext and field_ext:
            filename = f"{raw_filename}{field_ext}"
        else:
            filename = raw_filename

    if not is_allowed_file(filename):
        err_msg = f"Rejected PocketBase print job {rec_id}: file extension for '{filename}' is not allowed."
        logging.warning(err_msg)
        try:
            pb.collection('print_jobs').update(rec_id, {'status': 'failed', 'error_message': err_msg})
        except Exception as e:
            logging.warning(f"Failed to update status to 'failed' for {rec_id}: {e}")
        return

    logging.info(f"Processing PocketBase print job {rec_id} ({filename}, paper_size={paper_size})...")
    try:
        pb.collection('print_jobs').update(rec_id, {'status': 'printing'})
    except Exception as e:
        logging.warning(f"Failed to update status to 'printing' for {rec_id}: {e}")

    temp_filepath = None
    try:
        headers = {}
        query_params = {}
        if getattr(pb.auth_store, 'token', None):
            headers['Authorization'] = pb.auth_store.token
            try:
                file_token = pb.files.get_token()
                if file_token:
                    query_params['token'] = file_token
            except Exception:
                pass

        file_url = pb.files.get_url(record, file_field, query_params=query_params)

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(file_url, headers=headers)
            resp.raise_for_status()
            file_bytes = resp.content

        if len(file_bytes) > MAX_ATTACHMENT_SIZE:
            err_msg = f"Rejected PocketBase print job {rec_id}: file size ({len(file_bytes)} bytes) exceeds limit of {MAX_ATTACHMENT_SIZE} bytes."
            logging.warning(err_msg)
            try:
                pb.collection('print_jobs').update(rec_id, {'status': 'failed', 'error_message': err_msg})
            except Exception:
                pass
            return

        # If file has .pdf extension but lacks %PDF- header, check if it is plain text
        if filename.lower().endswith('.pdf') and not file_bytes.startswith(b'%PDF-'):
            try:
                file_bytes.decode('utf-8')
                new_filename = os.path.splitext(filename)[0] + '.txt'
                logging.warning(
                    f"Job {rec_id}: '{filename}' has .pdf extension but lacks %PDF- header. "
                    f"Content is valid text; auto-renaming to '{new_filename}' to prevent CUPS filter crash."
                )
                filename = new_filename
            except UnicodeDecodeError:
                pass

        safe_filename = sanitize_filename(filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}") as temp_file:
            temp_file.write(file_bytes)
            temp_filepath = temp_file.name

        success, msg = print_file(temp_filepath, filename, paper_size=paper_size)

        if success:
            pb.collection('print_jobs').update(rec_id, {'status': 'completed'})
            logging.info(f"PocketBase print job {rec_id} completed successfully.")
        else:
            pb.collection('print_jobs').update(rec_id, {'status': 'failed', 'error_message': str(msg)})
            logging.error(f"PocketBase print job {rec_id} failed: {msg}")

    except Exception as e:
        logging.error(f"Error processing PocketBase job {rec_id}: {e}")
        sentry_sdk.capture_exception(e)
        try:
            pb.collection('print_jobs').update(rec_id, {'status': 'failed', 'error_message': str(e)})
        except Exception:
            pass
    finally:
        if temp_filepath:
            try:
                os.remove(temp_filepath)
            except OSError as e:
                logging.warning(f"Failed to remove temp file {temp_filepath}: {e}")

def start_pocketbase_worker(url: str, user: str, password: str):
    if not HAS_POCKETBASE:
        logging.warning("pocketbase / httpx packages are not installed. Skipping PocketBase realtime worker.")
        return

    def _worker():
        logging.info("Starting PocketBase Realtime print worker...")
        base_url = url if "://" in url else f"https://{url}"
        parsed_pb = urllib.parse.urlparse(base_url)
        if parsed_pb.scheme.lower() != 'https':
            logging.error(f"Insecure POCKETBASE_URL scheme '{parsed_pb.scheme}://'. HTTPS is required.")
            return

        pb = None
        consecutive_errors = 0

        while not shutdown_event.is_set():
            try:
                # 1. Initialize PocketBase client with resilient timeouts (30s read/write, 10s connect)
                if pb is None:
                    pb = PocketBase(
                        base_url,
                        timeout=httpx.Timeout(30.0, connect=10.0)
                    )

                # 2. Authenticate only when token is missing or invalid
                if not getattr(pb.auth_store, 'is_valid', False):
                    logging.info(f"Authenticating to PocketBase at {base_url}...")
                    pb.collection("users").auth_with_password(user, password)
                    logging.info("PocketBase authentication successful.")

                def _process_pending_jobs():
                    try:
                        queued_jobs = pb.collection("print_jobs").get_full_list(
                            query_params={"filter": 'status = "queued"'}
                        )
                        if queued_jobs:
                            logging.info(f"Found {len(queued_jobs)} pending print job(s) in PocketBase.")
                            for job in queued_jobs:
                                if shutdown_event.is_set():
                                    break
                                process_pocketbase_record(pb, job)
                    except Exception as e:
                        logging.warning(f"Could not fetch queued PocketBase jobs: {e}")

                # Process any pending queued jobs on startup / reconnect
                _process_pending_jobs()

                def _on_event(event):
                    try:
                        action = getattr(event, 'action', '')
                        record = getattr(event, 'record', None)
                        if record and action in ('create', 'update'):
                            rec_status = getattr(record, 'status', '') or (record.get('status') if isinstance(record, dict) else '')
                            if rec_status == 'queued':
                                process_pocketbase_record(pb, record)
                    except Exception as ev_err:
                        logging.error(f"Error handling PocketBase event: {ev_err}")
                        sentry_sdk.capture_exception(ev_err)

                # Clean up local SSE state before (re-)subscribing without sending invalid client_id POSTs
                if getattr(pb.realtime, 'event_source', None):
                    try:
                        pb.realtime._disconnect()
                    except Exception:
                        pass
                if hasattr(pb.realtime, 'subscriptions'):
                    pb.realtime.subscriptions.clear()

                pb.collection("print_jobs").subscribe(_on_event)
                logging.info("Subscribed to PocketBase 'print_jobs' realtime events.")
                consecutive_errors = 0

                # Liveness check loop (check every 2 seconds for responsive reconnect)
                while not shutdown_event.is_set():
                    time.sleep(2)

                    if shutdown_event.is_set():
                        break

                    # 1. Check auth token validity
                    if not getattr(pb.auth_store, 'is_valid', False):
                        logging.warning("PocketBase auth token expired or invalid, re-authenticating...")
                        break

                    # 2. Check if SSE realtime stream thread is still alive
                    es = getattr(pb.realtime, 'event_source', None)
                    loop_thread = getattr(es, '_loop_thread', None) if es else None
                    if not es or not loop_thread or not loop_thread.is_alive():
                        logging.info("PocketBase realtime SSE stream closed (proxy timeout). Reconnecting...")
                        break

            except Exception as e:
                if shutdown_event.is_set():
                    break

                consecutive_errors += 1
                is_auth_error = getattr(e, 'status', None) in (401, 403) or "401" in str(e) or "403" in str(e)

                # Reset auth store on auth errors so next loop attempts fresh auth_with_password
                if is_auth_error and pb:
                    try:
                        pb.auth_store.clear()
                    except Exception:
                        pass

                # Clean up local SSE state
                if pb:
                    try:
                        pb.realtime._disconnect()
                    except Exception:
                        pass

                # Alert to Sentry only on repeated failures (>=3) or non-transient auth errors (>=2)
                if consecutive_errors >= 3 or (is_auth_error and consecutive_errors >= 2):
                    logging.error(f"PocketBase realtime worker repeated error ({consecutive_errors}x): {e}. Reconnecting in 10s...")
                    sentry_sdk.capture_exception(e)
                else:
                    logging.warning(f"PocketBase realtime worker error: {e}. Reconnecting in 10s (attempt {consecutive_errors})...")

                for _ in range(10):
                    if shutdown_event.is_set():
                        break
                    time.sleep(1)

        # Service shutdown cleanup
        if pb and getattr(pb.realtime, 'event_source', None):
            try:
                es = getattr(pb.realtime, 'event_source', None)
                loop_thread = getattr(es, '_loop_thread', None) if es else None
                if loop_thread and loop_thread.is_alive() and getattr(pb.realtime, 'client_id', ''):
                    pb.realtime.unsubscribe()
                else:
                    pb.realtime._disconnect()
            except Exception:
                pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

def process_messages(server, uids):
    if not uids:
        return
    
    logging.info(f"Processing {len(uids)} new message(s).")
    fetch_data = server.fetch(uids, ['RFC822'])
    
    for uid, data in fetch_data.items():
        if shutdown_event.is_set():
            break
        if b'RFC822' not in data:
            continue
            
        msg = email.message_from_bytes(data[b'RFC822'], policy=default)
        subject = msg.get('Subject', '<No Subject>')
        sender = msg.get('From', '<Unknown Sender>')
        logging.info(f"Reading message from '{sender}': {subject}")

        if not is_sender_allowed(sender):
            logging.warning(f"Ignoring email from unauthorized sender: {sender} (Subject: {subject})")
            continue
        
        has_attachments = False
        for part in msg.iter_attachments():
            if shutdown_event.is_set():
                break
            filename = part.get_filename()
            if filename:
                if not is_allowed_file(filename):
                    logging.warning(f"Skipping attachment '{filename}': file extension not allowed.")
                    continue

                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                if len(payload) > MAX_ATTACHMENT_SIZE:
                    logging.warning(f"Skipping attachment '{filename}': size ({len(payload)} bytes) exceeds limit of {MAX_ATTACHMENT_SIZE} bytes.")
                    continue

                has_attachments = True
                logging.info(f"Found attachment: {filename} ({len(payload)} bytes)")
                
                safe_filename = sanitize_filename(filename)
                temp_filepath = None
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}") as temp_file:
                        temp_file.write(payload)
                        temp_filepath = temp_file.name
                    
                    print_file(temp_filepath, filename)
                finally:
                    if temp_filepath:
                        try:
                            os.remove(temp_filepath)
                        except OSError as e:
                            logging.warning(f"Failed to remove temp file {temp_filepath}: {e}")
        
        if not has_attachments:
            logging.info(f"No valid printable attachments found in message: {subject}")

def main():
    if not all([HOST, USERNAME, PASSWORD]):
        logging.error("IMAP credentials are not fully set in the environment.")
        return

    if ALLOWED_SENDERS:
        logging.info(f"Sender allowlist active ({len(ALLOWED_SENDERS)} rule(s)): {', '.join(ALLOWED_SENDERS)}")
    else:
        logging.info("ALLOWED_SENDERS not set; public access enabled for incoming emails.")

    logging.info(f"Allowed file extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}")
    logging.info(f"Max attachment size: {MAX_ATTACHMENT_SIZE / (1024 * 1024):.1f} MB (page limit: {PAGE_LIMIT})")

    if UPTIME_KUMA_PUSH_URL:
        start_heartbeat_worker(UPTIME_KUMA_PUSH_URL, HEARTBEAT_INTERVAL)

    if all([PB_URL, PB_USER, PB_PASSWORD]):
        start_pocketbase_worker(PB_URL, PB_USER, PB_PASSWORD)
    else:
        logging.info("PocketBase credentials not configured. Running in IMAP-only mode.")

    ssl_context = ssl.create_default_context()

    while not shutdown_event.is_set():
        try:
            set_service_state('starting', f'Connecting to {HOST}')
            logging.info(f"Connecting to IMAP server: {HOST}")
            with IMAPClient(HOST, ssl_context=ssl_context) as server:
                server.login(USERNAME, PASSWORD)
                logging.info(f"Logged in as {USERNAME}")
                server.select_folder('INBOX')
                
                # Fetch any unread messages before entering IDLE
                uids = server.search(['UNSEEN'])
                if uids:
                    set_service_state('up', f'Processing {len(uids)} unread message(s)')
                    process_messages(server, uids)
                    server.add_flags(uids, [b'\\Seen'])

                if shutdown_event.is_set():
                    break

                set_service_state('up', 'Connected (IDLE mode)')
                logging.info("Entering IDLE mode. Waiting for new emails...")
                server.idle()
                
                idle_start_time = time.time()
                while not shutdown_event.is_set():
                    # Check IDLE with a short timeout to be responsive to shutdown_event
                    responses = server.idle_check(timeout=10.0)
                    if responses or (time.time() - idle_start_time > 10.0 * 60):
                        server.idle_done()
                        if responses:
                            new_uids = server.search(['UNSEEN'])
                            if new_uids:
                                set_service_state('up', f'Processing {len(new_uids)} new message(s)')
                                process_messages(server, new_uids)
                                server.add_flags(new_uids, [b'\\Seen'])
                                
                        if shutdown_event.is_set():
                            break

                        set_service_state('up', 'Connected (IDLE mode)')
                        server.idle()
                        idle_start_time = time.time()

                try:
                    server.idle_done()
                except Exception:
                    pass
                try:
                    server.logout()
                except Exception:
                    pass

        except Exception as e:
            if shutdown_event.is_set():
                break
            set_service_state('down', f'Error: {str(e)[:50]}')
            logging.error(f"Connection lost or error occurred: {e}")
            logging.info("Reconnecting in 10 seconds...")
            for _ in range(10):
                if shutdown_event.is_set():
                    break
                time.sleep(1)

    logging.info("Email to Print service stopped gracefully.")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Email to Print Server")
    parser.add_argument('--test-print', type=str, help="Path to a file to test printing directly")
    args = parser.parse_args()

    if args.test_print:
        if os.path.exists(args.test_print):
            filename = os.path.basename(args.test_print)
            if not is_allowed_file(filename):
                logging.error(f"Cannot print '{filename}': file extension not in allowed extensions ({', '.join(sorted(ALLOWED_EXTENSIONS))})")
            else:
                logging.info(f"Running in test mode. Printing {filename}...")
                print_file(args.test_print, filename)
        else:
            logging.error(f"Test file not found: {args.test_print}")
    else:
        main()

