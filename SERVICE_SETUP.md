# Email to Print Service Setup

Instructions for setting up and managing Email to Print as a background user service on Linux (via systemd user services) and macOS (via launchd). Neither service requires root/sudo privileges.

## Prerequisites

1. Have `uv` installed.
2. Have your `.env` file configured.
3. Have your environment configured with a working `lp` command (standard on Linux with CUPS, and standard on macOS).

## Installation

We provide automated setup scripts for both Linux and macOS. Neither script should be run with `sudo`.

### Linux (Systemd User Service)

The Linux setup script configures a user-level `systemd` service (`~/.config/systemd/user/emailtoprint.service`). Do **not** run as root.

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

> **Note for headless servers:** If you want the service to start automatically on boot and stay running after you log out of SSH, enable lingering for your user:
> ```bash
> loginctl enable-linger $USER
> ```

### macOS (Launchd)

The macOS setup script configures a user-level `LaunchAgent` (`~/Library/LaunchAgents/com.emailtoprint.service.plist`). Do **not** run as root.

```bash
chmod +x setup_macos.sh
./setup_macos.sh
```

## Managing the Service

### Linux

Use standard `systemctl --user` commands:

**Check the status:**
```bash
systemctl --user status emailtoprint.service
```

**Restart the service (e.g., after modifying code or .env):**
```bash
systemctl --user restart emailtoprint.service
```

**Stop the service:**
```bash
systemctl --user stop emailtoprint.service
```

**View Logs:**
```bash
journalctl --user -u emailtoprint.service -f
```

### macOS

The service is managed automatically by `launchd` once loaded. 

**Restart the service:**
```bash
launchctl kickstart -k gui/$(id -u)/com.emailtoprint.service
```
*(Or manually reload the plist: `launchctl unload ~/Library/LaunchAgents/com.emailtoprint.service.plist && launchctl load -w ~/Library/LaunchAgents/com.emailtoprint.service.plist`)*

**View Logs:**
Logs on macOS are written to standard files in the project directory:
```bash
tail -f output.log error.log
```

## Updating

To check for the latest changes, update dependencies, and restart the service, you can run the provided update scripts (also scheduled automatically via nightly cron at 3 AM):

**Linux:**
```bash
chmod +x update_linux.sh
./update_linux.sh
```

**macOS:**
```bash
chmod +x update_macos.sh
./update_macos.sh
```
