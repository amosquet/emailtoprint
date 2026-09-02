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

def print_file(filepath, filename):
    cmd = ['lp']
    if PRINTER_NAME:
        cmd.extend(['-d', PRINTER_NAME])
    cmd.extend([
        '-o', 'media=Letter',
        '-o', 'sides=two-sided-long-edge',
        '-o', f'page-ranges=1-{PAGE_LIMIT}',
        filepath
    ])
    try:
        logging.info(f"Printing {filename} (up to {PAGE_LIMIT} pages)...")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        out = result.stdout.strip()
        logging.info(f"Print job queued successfully: {out}")
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
    filename = getattr(record, 'filename', '') or (record.get('filename') if isinstance(record, dict) else 'document.pdf')
    file_field = getattr(record, 'file', '') or (record.get('file') if isinstance(record, dict) else '')
    
    if status != 'queued' or not file_field:
        return

    if not is_allowed_file(filename):
        err_msg = f"Rejected PocketBase print job {rec_id}: file extension for '{filename}' is not allowed."
        logging.warning(err_msg)
        try:
            pb.collection('print_jobs').update(rec_id, {'status': 'failed', 'error_message': err_msg})
        except Exception as e:
            logging.warning(f"Failed to update status to 'failed' for {rec_id}: {e}")
        return

    logging.info(f"Processing PocketBase print job {rec_id} ({filename})...")
    try:
        pb.collection('print_jobs').update(rec_id, {'status': 'printing'})
    except Exception as e:
        logging.warning(f"Failed to update status to 'printing' for {rec_id}: {e}")

    temp_filepath = None
    try:
        file_url = pb.get_file_url(record, file_field)
        headers = {}
        if getattr(pb.auth_store, 'token', None):
            headers['Authorization'] = pb.auth_store.token

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

        safe_filename = sanitize_filename(filename)
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{safe_filename}") as temp_file:
            temp_file.write(file_bytes)
            temp_filepath = temp_file.name

        success, msg = print_file(temp_filepath, filename)

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
        while not shutdown_event.is_set():
            pb = None
            try:
                base_url = url if "://" in url else f"https://{url}"
                parsed_pb = urllib.parse.urlparse(base_url)
                if parsed_pb.scheme.lower() != 'https':
                    logging.error(f"Insecure POCKETBASE_URL scheme '{parsed_pb.scheme}://'. HTTPS is required.")
                    for _ in range(30):
                        if shutdown_event.is_set():
                            break
                        time.sleep(1)
                    continue

                pb = PocketBase(base_url)
                pb.collection("users").auth_with_password(user, password)
                logging.info(f"Authenticated to PocketBase at {base_url}")

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

                pb.collection("print_jobs").subscribe(_on_event)
                logging.info("Subscribed to PocketBase 'print_jobs' realtime events.")

                poll_counter = 0
                while not shutdown_event.is_set():
                    for _ in range(30):
                        if shutdown_event.is_set():
                            break
                        time.sleep(1)

                    if shutdown_event.is_set():
                        break

                    # 1. Check auth token validity
                    if not getattr(pb.auth_store, 'is_valid', False):
                        logging.warning("PocketBase auth invalid, reconnecting...")
                        break

                    # 2. Check if SSE realtime stream thread is still alive
                    es = getattr(pb.realtime, 'event_source', None)
                    loop_thread = getattr(es, '_loop_thread', None) if es else None
                    if not es or not loop_thread or not loop_thread.is_alive():
                        logging.warning("PocketBase realtime SSE stream disconnected. Reconnecting...")
                        break

                    # 3. Fallback poll every 30s to catch any missed queued jobs
                    _process_pending_jobs()

            except Exception as e:
                if shutdown_event.is_set():
                    break
                logging.error(f"PocketBase realtime worker error: {e}. Reconnecting in 15 seconds...")
                sentry_sdk.capture_exception(e)
                for _ in range(15):
                    if shutdown_event.is_set():
                        break
                    time.sleep(1)
            finally:
                if pb:
                    try:
                        pb.realtime.unsubscribe()
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

