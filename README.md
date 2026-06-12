# Email to Print

A Python service that monitors an IMAP email inbox and automatically prints any attachments it receives using CUPS (`lp` command). It's designed to run continuously as a background service on macOS or Linux and includes automatic nightly updates.

> **Note:** This service has currently only been tested on macOS using an HP Color LaserJet MFP 281fdw.

## Features

- **Automatic Printing:** Listens for new emails via IMAP IDLE and prints attachments automatically.
- **CUPS Integration:** Uses the standard `lp` command to interact with your configured printers.
- **Cross-Platform Services:** Includes setup scripts for macOS (`launchd`) and Linux (`systemd`).
- **Auto-Updates:** Sets up cron jobs to pull the latest changes nightly.
- **Sentry Integration:** Optional Sentry support for error tracking and monitoring.
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

   # Sentry Monitoring (Optional)
   SENTRY_DSN=your_sentry_dsn_here

   # Print Configuration
   PAGE_LIMIT=5
   PRINTER_NAME=bigboi
   ```

   *Note: For Gmail or other modern providers, you will likely need to generate an "App Password" rather than using your primary account password.*

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