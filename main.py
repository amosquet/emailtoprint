import os
import email
from email.policy import default
import subprocess
import tempfile
import time
import logging

from dotenv import load_dotenv
from imapclient import IMAPClient
import sentry_sdk

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

load_dotenv()

SENTRY_DSN = os.getenv('SENTRY_DSN')
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
    )
    logging.info("Sentry integration initialized for health monitoring.")

HOST = os.getenv('IMAP_HOST')
USERNAME = os.getenv('IMAP_USERNAME')
PASSWORD = os.getenv('IMAP_PASSWORD')
PAGE_LIMIT = os.getenv('PAGE_LIMIT', '5')

def print_file(filepath, filename):
    cmd = [
        'lp', 
        '-o', f'page-ranges=1-{PAGE_LIMIT}', 
        '-o', 'Duplex=DuplexNoTumble', 
        '-o', 'Collate=True', 
        filepath
    ]
    try:
        logging.info(f"Printing {filename} (up to {PAGE_LIMIT} pages)...")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        logging.info(f"Print job queued successfully: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to print {filename}. Error: {e.stderr.strip() if e.stderr else e}")
    except FileNotFoundError:
        logging.error("The 'lp' command was not found. Is CUPS installed?")

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

    while True:
        try:
            logging.info(f"Connecting to IMAP server: {HOST}")
            with IMAPClient(HOST) as server:
                server.login(USERNAME, PASSWORD)
                logging.info(f"Logged in as {USERNAME}")
                server.select_folder('INBOX')
                
                # Fetch any unread messages before entering IDLE
                uids = server.search(['UNSEEN'])
                if uids:
                    process_messages(server, uids)
                    server.add_flags(uids, [b'\\Seen'])

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
                            process_messages(server, new_uids)
                            server.add_flags(new_uids, [b'\\Seen'])
                            
                        # Re-enter IDLE
                        server.idle()
                        
        except Exception as e:
            logging.error(f"Connection lost or error occurred: {e}")
            logging.info("Reconnecting in 10 seconds...")
            time.sleep(10)

if __name__ == '__main__':
    main()
