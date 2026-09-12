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

To check for the latest changes, update dependencies, and restart the service, you can run the provided update scripts. Both platforms also run automatic nightly checks at 3 AM:
- **macOS:** Scheduled natively via user LaunchAgent (`~/Library/LaunchAgents/com.emailtoprint.updater.plist`). Logs are written to `update.log`.
- **Linux:** Scheduled via user `cron`. Logs are written to `update.log`.

**Linux Manual Run:**
```bash
chmod +x update_linux.sh
./update_linux.sh
```

**macOS Manual Run:**
```bash
chmod +x update_macos.sh
./update_macos.sh
```

**macOS Trigger Update LaunchAgent Now:**
```bash
launchctl start com.emailtoprint.updater
```

## macOS Permissions & Troubleshooting (Monterey & Newer)

macOS Monterey (12.x) and newer enforce strict Privacy / TCC (Transparency, Consent, and Control) security on user folders (`~/Documents`, `~/Desktop`, and `~/Downloads`).

If your repository is stored inside `~/Documents` (e.g., `~/Documents/GitHub/emailtoprint`), background `launchd` jobs (or legacy `cron`) will fail with:
```
Operation not permitted
```

### Solutions:

1. **Recommended:** Move the repository outside TCC-protected directories, for example:
   ```bash
   mkdir -p ~/Projects
   mv ~/Documents/GitHub/emailtoprint ~/Projects/
   cd ~/Projects/emailtoprint
   ./setup_macos.sh
   ```
   Folders like `~/Projects` or `~/Developer` are not restricted by macOS TCC and run without background permission blocks.

2. **Alternative:** If you must keep the repository in `~/Documents`, grant Full Disk Access to `/bin/bash` (and `/bin/zsh` if used):
   - Open **System Preferences** ( > System Preferences).
   - Go to **Security & Privacy** > **Privacy** tab.
   - Select **Full Disk Access** on the left sidebar.
   - Click the lock icon in the lower-left corner to make changes.
   - Click the **+** button, press `Command + Shift + G`, enter `/bin/bash`, and add it.
   - Repeat for `/bin/zsh` if needed.
