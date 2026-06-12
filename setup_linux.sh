#!/bin/bash

# Email to Print Service Setup Script
# This script automates the installation of the emailtoprint systemd service.

set -e

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root (or using sudo)"
  exit 1
fi

echo "🚀 Starting Email to Print service setup..."

# 1. Sync the environment
USER_NAME=${SUDO_USER:-$(whoami)}
echo "📦 Syncing environment with uv as $USER_NAME..."
su - "$USER_NAME" -c "cd $(pwd) && uv sync"

# 2. Copy the service file
echo "📄 Copying emailtoprint.service to /etc/systemd/system/..."
cp emailtoprint.service /etc/systemd/system/

# 3. Reload systemd daemon
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

# 4. Enable the service
echo "✅ Enabling emailtoprint service to start on boot..."
systemctl enable emailtoprint.service

# 5. Start/Restart the service
echo "▶️  Starting emailtoprint service..."
systemctl restart emailtoprint.service

# 6. Setup automatic updates via cron
echo "⏰ Setting up automatic nightly updates..."
chmod +x "$(pwd)/update_linux.sh"
CRON_JOB="0 3 * * * $(pwd)/update_linux.sh >> $(pwd)/update.log 2>&1"
(crontab -u "$USER_NAME" -l 2>/dev/null | grep -v "update_linux.sh" || true; echo "$CRON_JOB") | crontab -u "$USER_NAME" -

echo "🎉 Setup complete! You can check the status with:"
echo "   systemctl status emailtoprint.service"
echo "   journalctl -u emailtoprint.service -f"
