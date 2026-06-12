#!/bin/bash

# Email to Print Service Setup Script for MacOS
# This script automates the installation of the emailtoprint launchd service.

set -e

echo "🚀 Starting Email to Print service setup for MacOS..."

# Ensure the script is NOT run as root
if [ "$EUID" -eq 0 ]; then
  echo "❌ Please DO NOT run as root (no sudo needed for MacOS user services)"
  exit 1
fi

PROJECT_DIR="$(pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/com.emailtoprint.service.plist"
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"

# 1. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# 2. Generate the LaunchAgent plist
echo "📄 Generating LaunchAgent plist at $PLIST_PATH..."
mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.emailtoprint.service</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/error.log</string>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/output.log</string>
</dict>
</plist>
EOF

# 3. Load the launchd service
echo "🔄 Loading launchd service..."
# Unload it first if it already exists
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

# 4. Setup automatic updates via cron
echo "⏰ Setting up automatic nightly updates..."
chmod +x "$PROJECT_DIR/update_macos.sh"
CRON_JOB="0 3 * * * $PROJECT_DIR/update_macos.sh >> $PROJECT_DIR/update.log 2>&1"
(crontab -l 2>/dev/null | grep -v "update_macos.sh" || true; echo "$CRON_JOB") | crontab -

echo "🎉 Setup complete! You can check the logs with:"
echo "   tail -f $PROJECT_DIR/output.log"
echo "   tail -f $PROJECT_DIR/error.log"
