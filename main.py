import os
import email
from email.policy import default
import subprocess
import tempfile
import time
import logging
import argparse

import urllib.parse
import urllib.request
import threading

from dotenv import load_dotenv
from imapclient import IMAPClient
import sentry_sdk

try:
    from pocketbase import PocketBase
    import httpx
    HAS_POCKETBASE = True
except ImportError:
    HAS_POCKETBASE = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
    )
    logging.info("Sentry integration initialized for health monitoring.")

UPTIME_KUMA_PUSH_URL = os.getenv('UPTIME_KUMA_PUSH_URL')
try:
    HEARTBEAT_INTERVAL = int(os.getenv('HEARTBEAT_INTERVAL', '60'))
except ValueError:
    HEARTBEAT_INTERVAL = 60

HOST = os.getenv('IMAP_HOST')
USERNAME = os.getenv('IMAP_USERNAME')
PASSWORD = os.getenv('IMAP_PASSWORD')
PAGE_LIMIT = os.getenv('PAGE_LIMIT', '5')
PRINTER_NAME = os.getenv('PRINTER_NAME', '')

PB_URL = os.getenv('POCKETBASE_URL')
PB_USER = os.getenv('POCKETBASE_USER')
PB_PASSWORD = os.getenv('POCKETBASE_PASSWORD')

service_state = {
    'status': 'starting',
    'msg': 'Service starting',
}

def send_heartbeat(push_url, status='up', msg='OK'):
    try:
        parsed = urllib.parse.urlparse(push_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        query_params['status'] = [status]
        query_params['msg'] = [msg]

        new_query = urllib.parse.urlencode(query_params, doseq=True)
        target_url = urllib.parse.urlunparse(parsed._replace(query=new_query))

        req = urllib.request.Request(
            target_url,
            headers={'User-Agent': 'emailtoprint-heartbeat/1.0'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            pass
        logging.debug(f"Heartbeat sent successfully (status={status}, msg={msg})")
    except Exception as e:
        logging.warning(f"Failed to send heartbeat to Uptime Kuma: {e}")

def start_heartbeat_worker(push_url, interval):
    def _worker():
        logging.info(f"Uptime Kuma heartbeat worker started (interval: {interval}s).")
        while True:
            status = service_state.get('status', 'up')
            msg = service_state.get('msg', 'OK')
            send_heartbeat(push_url, status=status, msg=msg)
            time.sleep(interval)

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

    logging.info(f"Processing PocketBase print job {rec_id} ({filename})...")
    try:
        pb.collection('print_jobs').update(rec_id, {'status': 'printing'})
    except Exception as e:
        logging.warning(f"Failed to update status to 'printing' for {rec_id}: {e}")

    try:
        file_url = pb.get_file_url(record, file_field)
        headers = {}
        if getattr(pb.auth_store, 'token', None):
            headers['Authorization'] = pb.auth_store.token

        with httpx.Client(timeout=30.0) as client:
            resp = client.get(file_url, headers=headers)
            resp.raise_for_status()
            file_bytes = resp.content

        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
            temp_file.write(file_bytes)
            temp_filepath = temp_file.name

        success, msg = print_file(temp_filepath, filename)
        
        try:
            os.remove(temp_filepath)
        except OSError as e:
            logging.warning(f"Failed to remove temp file {temp_filepath}: {e}")

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

def start_pocketbase_worker(url: str, user: str, password: str):
    if not HAS_POCKETBASE:
        logging.warning("pocketbase / httpx packages are not installed. Skipping PocketBase realtime worker.")
        return

    def _worker():
        logging.info("Starting PocketBase Realtime print worker...")
        while True:
            try:
                base_url = url if "://" in url else f"https://{url}"
                pb = PocketBase(base_url)
                pb.collection("users").auth_with_password(user, password)
                logging.info(f"Authenticated to PocketBase at {base_url}")

                # Process any pending queued jobs on startup / reconnect
                try:
                    queued_jobs = pb.collection("print_jobs").get_full_list(
                        query_params={"filter": 'status = "queued"'}
                    )
                    if queued_jobs:
                        logging.info(f"Found {len(queued_jobs)} pending print job(s) in PocketBase.")
                        for job in queued_jobs:
                            process_pocketbase_record(pb, job)
                except Exception as e:
                    logging.warning(f"Could not fetch initial queued PocketBase jobs: {e}")

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

                while True:
                    time.sleep(30)
                    if not getattr(pb.auth_store, 'is_valid', False):
                        logging.warning("PocketBase auth invalid, reconnecting...")
                        break

            except Exception as e:
                logging.error(f"PocketBase realtime worker error: {e}. Reconnecting in 15 seconds...")
                sentry_sdk.capture_exception(e)
                time.sleep(15)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

def process_messages(server, uids):
    if not uids:
        return
    
    logging.info(f"Processing {len(uids)} new message(s).")
    fetch_data = server.fetch(uids, ['RFC822'])
    
    for uid, data in fetch_data.items():
        if b'RFC822' not in data:
            continue
            
        msg = email.message_from_bytes(data[b'RFC822'], policy=default)
        subject = msg.get('Subject', '<No Subject>')
        logging.info(f"Reading message: {subject}")
        
        has_attachments = False
        for part in msg.iter_attachments():
            filename = part.get_filename()
            if filename:
                has_attachments = True
                logging.info(f"Found attachment: {filename}")
                payload = part.get_payload(decode=True)
                if payload:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
                        temp_file.write(payload)
                        temp_filepath = temp_file.name
                    
                    print_file(temp_filepath, filename)
                    
                    try:
                        os.remove(temp_filepath)
                    except OSError as e:
                        logging.warning(f"Failed to remove temp file {temp_filepath}: {e}")
        
        if not has_attachments:
            logging.info(f"No attachments found in message: {subject}")

def main():
    if not all([HOST, USERNAME, PASSWORD]):
        logging.error("IMAP credentials are not fully set in the environment.")
        return

    if UPTIME_KUMA_PUSH_URL:
        start_heartbeat_worker(UPTIME_KUMA_PUSH_URL, HEARTBEAT_INTERVAL)

    if all([PB_URL, PB_USER, PB_PASSWORD]):
        start_pocketbase_worker(PB_URL, PB_USER, PB_PASSWORD)
    else:
        logging.info("PocketBase credentials not configured. Running in IMAP-only mode.")

    while True:
        try:
            service_state['status'] = 'starting'
            service_state['msg'] = f'Connecting to {HOST}'
            logging.info(f"Connecting to IMAP server: {HOST}")
            with IMAPClient(HOST) as server:
                server.login(USERNAME, PASSWORD)
                logging.info(f"Logged in as {USERNAME}")
                server.select_folder('INBOX')
                
                # Fetch any unread messages before entering IDLE
                uids = server.search(['UNSEEN'])
                if uids:
                    service_state['status'] = 'up'
                    service_state['msg'] = f'Processing {len(uids)} unread message(s)'
                    process_messages(server, uids)
                    server.add_flags(uids, [b'\\Seen'])

                service_state['status'] = 'up'
                service_state['msg'] = 'Connected (IDLE mode)'
                logging.info("Entering IDLE mode. Waiting for new emails...")
                server.idle()
                
                while True:
                    # 29 minutes is the RFC 2177 recommended maximum IDLE timeout
                    responses = server.idle_check(timeout=29.0 * 60)
                    if responses:
                        # We must exit IDLE before we can run other commands
                        server.idle_done()
                        
                        new_uids = server.search(['UNSEEN'])
                        if new_uids:
                            service_state['status'] = 'up'
                            service_state['msg'] = f'Processing {len(new_uids)} new message(s)'
                            process_messages(server, new_uids)
                            server.add_flags(new_uids, [b'\\Seen'])
                            
                        # Re-enter IDLE
                        service_state['status'] = 'up'
                        service_state['msg'] = 'Connected (IDLE mode)'
                        server.idle()
                        
        except Exception as e:
            service_state['status'] = 'down'
            service_state['msg'] = f'Error: {str(e)[:50]}'
            logging.error(f"Connection lost or error occurred: {e}")
            logging.info("Reconnecting in 10 seconds...")
            time.sleep(10)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Email to Print Server")
    parser.add_argument('--test-print', type=str, help="Path to a file to test printing directly")
    args = parser.parse_args()

    if args.test_print:
        if os.path.exists(args.test_print):
            filename = os.path.basename(args.test_print)
            logging.info(f"Running in test mode. Printing {filename}...")
            print_file(args.test_print, filename)
        else:
            logging.error(f"Test file not found: {args.test_print}")
    else:
        main()

