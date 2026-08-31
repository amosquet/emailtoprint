# Email to Print

A Python service that monitors an IMAP email inbox and automatically prints any attachments it receives using CUPS (`lp` command). It's designed to run continuously as a background service on macOS or Linux and includes automatic nightly updates.

> **Note:** This service has been tested on Arch Linux and macOS Monterey using an HP Color LaserJet MFP 281fdw and HP LaserJet Flow MFP M630.

## Features

- **Automatic Printing:** Listens for new emails via IMAP IDLE and prints attachments automatically.
- **PocketBase Realtime Queue (Optional):** Subscribes in realtime to a PocketBase `print_jobs` collection to receive and process print jobs dispatched from bots (like Shisho) across networks.
- **CUPS Integration:** Uses the standard `lp` command to interact with your configured printers.
- **Cross-Platform Services:** Includes setup scripts for macOS (`launchd`) and Linux (`systemd`).
- **Auto-Updates:** Sets up cron jobs to pull the latest changes nightly.
- **Sentry Integration:** Optional Sentry support for error tracking and monitoring.
- **Uptime Kuma Heartbeat:** Optional push-based heartbeat monitoring to track service uptime and IMAP status.
- **Two-Sided Printing:** Prints documents double-sided on letter paper by default.

## Prerequisites

- Python 3.14+
- `uv` (Python package manager)
- CUPS installed and a printer configured.
- An email account with IMAP access enabled.

## Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
2. Edit the `.env` file with your credentials and printer details:

   ```env
   # IMAP Configuration
   IMAP_HOST=imap.example.com
   IMAP_USERNAME=your_email@example.com
   IMAP_PASSWORD=your_app_password

   # PocketBase Realtime Queue (Optional)
   POCKETBASE_URL=https://pocketbase.example.com
   POCKETBASE_USER=admin@example.com
   POCKETBASE_PASSWORD=your_pb_password

   # Sentry Monitoring (Optional)
   SENTRY_DSN=your_sentry_dsn_here
   SENTRY_TRACES_SAMPLE_RATE=0.1

   # Uptime Kuma Push / Heartbeat Monitoring (Optional)
   UPTIME_KUMA_PUSH_URL=https://uptime.example.com/api/push/key?status=up&msg=OK&ping=
   HEARTBEAT_INTERVAL=60

   # Security & Access Control Configuration
   # Comma-separated list of allowed sender email addresses or domains (Optional - leave blank for public access)
   ALLOWED_SENDERS=user@example.com,@trusteddomain.com

   # Comma-separated list of allowed file extensions (Default: .pdf,.jpg,.jpeg,.png,.txt,.doc,.docx)
   ALLOWED_EXTENSIONS=.pdf,.jpg,.jpeg,.png,.txt,.doc,.docx

   # Maximum attachment size in bytes (Default: 6291456 / 6MB)
   MAX_ATTACHMENT_SIZE=6291456

   # Print Configuration
   PAGE_LIMIT=5
   PRINTER_NAME=bigboi
   ```

   _Note: PocketBase is completely optional. If omitted, the service will run in IMAP-only mode._
   _Tip: Protect your credentials by restricting permissions: `chmod 600 .env`._

## Installation & Setup

The project includes scripts to easily install dependencies and configure the background service.

### macOS

Run the macOS setup script. This will use `uv` to install dependencies and configure a `launchd` service for your user.

```bash
./setup_macos.sh
```

Logs will be available in the project directory as `output.log` and `error.log`.

### Linux

Run the Linux setup script. This will use `uv` to install dependencies and configure a `systemd` user service.

```bash
./setup_linux.sh
```

## Manual Usage / Testing

You can run the script manually or test the printing functionality without running the full IMAP server listener.

First, sync the environment using `uv`:

```bash
uv sync
```

To start the server manually:

```bash
uv run python main.py
```

To test printing a specific file directly:

```bash
uv run python main.py --test-print /path/to/test_document.pdf
```

## Monitoring with Uptime Kuma

To monitor `emailtoprint` using Uptime Kuma's push (heartbeat) monitor:

1. In Uptime Kuma, click **Add New Monitor**.
2. Select **Monitor Type** as **Push**.
3. Choose a friendly name (e.g. `Email to Print`).
4. Set the **Heartbeat Interval** (e.g. `60` seconds) and **Retries**.
5. Copy the generated Push URL and paste it as `UPTIME_KUMA_PUSH_URL` in your `.env` file:
   ```env
   UPTIME_KUMA_PUSH_URL=https://uptime.example.com/api/push/YOUR_KEY?status=up&msg=OK&ping=
   HEARTBEAT_INTERVAL=60
   ```
6. Restart the service (`launchctl` or `systemctl --user`). The service will periodically report its connection and IDLE status to Uptime Kuma.

