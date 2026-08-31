#!/bin/bash

# Email to Print Service Setup Script for Linux
# This script automates the installation of the emailtoprint systemd user service.

set -e

echo "🚀 Starting Email to Print service setup for Linux..."

# Ensure the script is NOT run as root
if [ "$EUID" -eq 0 ]; then
  echo "❌ Please DO NOT run as root (no sudo needed for systemd user services)"
  exit 1
fi

# Ensure PATH includes common installation directories for uv and git
export PATH="/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_PATH="$SERVICE_DIR/emailtoprint.service"
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"

# 1. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# Ensure secure permissions on .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
  chmod 600 "$PROJECT_DIR/.env"
  echo "🔒 Secured permissions on .env (chmod 600)"
fi

# 2. Generate the systemd user service file
echo "📄 Generating systemd user service at $SERVICE_PATH..."
mkdir -p "$SERVICE_DIR"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Email to Print Service
After=network.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$PYTHON_PATH main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

# 3. Reload systemd user daemon
echo "🔄 Reloading systemd user daemon..."
systemctl --user daemon-reload

# 4. Enable the service
echo "✅ Enabling emailtoprint service to start on boot..."
systemctl --user enable emailtoprint.service

# 5. Start/Restart the service
echo "▶️  Starting emailtoprint service..."
systemctl --user restart emailtoprint.service

# 6. Setup automatic updates via cron
echo "⏰ Setting up automatic nightly updates..."
chmod +x "$PROJECT_DIR/update_linux.sh"
CRON_JOB="0 3 * * * $PROJECT_DIR/update_linux.sh >> $PROJECT_DIR/update.log 2>&1"
(crontab -l 2>/dev/null | grep -v "update_linux.sh" || true; echo "$CRON_JOB") | crontab -

echo "🎉 Setup complete! You can check the status with:"
echo "   systemctl --user status emailtoprint.service"
echo "   journalctl --user -u emailtoprint.service -f"
echo ""
echo "💡 Tip for headless servers: Run 'loginctl enable-linger $USER' to keep the service running after logging out."
