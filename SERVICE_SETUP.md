# Email to Print Service Setup

Instructions for setting up and managing Email to Print as a background service on Linux (via systemd) and MacOS (via launchd).

## Prerequisites

1. Have `uv` installed.
2. Have your `.env` file configured.
3. Have your environment configured with a working `lp` command (standard on Linux with CUPS, and standard on MacOS).

## Installation

We provide automated setup scripts for both Linux and MacOS.

### Linux (Systemd)

The Linux setup script must be run as root to install the systemd service.

```bash
chmod +x setup_linux.sh
sudo ./setup_linux.sh
```

### MacOS (Launchd)

The MacOS setup script configures a user-level `LaunchAgent`. It should **not** be run as root.

```bash
chmod +x setup_macos.sh
./setup_macos.sh
```

## Managing the Service

### Linux

You can use standard `systemctl` commands to manage the service.

**Check the status:**
```bash
sudo systemctl status emailtoprint.service
```

**Restart the service (e.g., after modifying code or .env):**
```bash
sudo systemctl restart emailtoprint.service
```

**Stop the service:**
```bash
sudo systemctl stop emailtoprint.service
```

**View Logs:**
```bash
sudo journalctl -u emailtoprint.service -f
```

### MacOS

The service is managed automatically by `launchd` once loaded. 

**Restart the service:**
```bash
launchctl unload ~/Library/LaunchAgents/com.emailtoprint.service.plist
launchctl load -w ~/Library/LaunchAgents/com.emailtoprint.service.plist
```

**View Logs:**
Logs on MacOS are written to standard files in the project directory.
```bash
tail -f output.log error.log
```

## Updating

To quickly pull the latest changes, update dependencies, and restart the service, you can use the provided update scripts:

**Linux:**
```bash
chmod +x update_linux.sh
./update_linux.sh
```

**MacOS:**
```bash
chmod +x update_macos.sh
./update_macos.sh
```
