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

# Ensure PATH includes common installation directories for uv and git
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
PLIST_PATH="$HOME/Library/LaunchAgents/com.emailtoprint.service.plist"
UPDATER_PLIST_PATH="$HOME/Library/LaunchAgents/com.emailtoprint.updater.plist"
PYTHON_PATH="$PROJECT_DIR/.venv/bin/python"

# Check if running from a TCC-protected directory on macOS (Documents, Desktop, Downloads)
if [[ "$PROJECT_DIR" == "$HOME/Documents"* ]] || [[ "$PROJECT_DIR" == "$HOME/Desktop"* ]] || [[ "$PROJECT_DIR" == "$HOME/Downloads"* ]]; then
  echo ""
  echo "⚠️  WARNING: Project is located in a protected macOS directory:"
  echo "   $PROJECT_DIR"
  echo "   macOS Monterey (and newer) blocks background launchd services from accessing Documents/Desktop/Downloads."
  echo "   RECOMMENDATION: Move this repository to a non-protected folder such as ~/Projects/emailtoprint or ~/emailtoprint."
  echo "   Alternatively, grant Full Disk Access to /bin/bash in:"
  echo "     System Preferences > Security & Privacy > Privacy > Full Disk Access"
  echo ""
fi

# 1. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# Ensure secure permissions on .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
  chmod 600 "$PROJECT_DIR/.env"
  echo "🔒 Secured permissions on .env (chmod 600)"
fi

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

# 4. Setup automatic updates via LaunchAgent
echo "⏰ Setting up automatic nightly updates via LaunchAgent at $UPDATER_PLIST_PATH..."
chmod +x "$PROJECT_DIR/update_macos.sh"

# Clean up legacy cron job if present
if crontab -l 2>/dev/null | grep -q "update_macos.sh"; then
  echo "🧹 Removing legacy cron job..."
  (crontab -l 2>/dev/null | grep -v "update_macos.sh" || true) | crontab -
fi

cat > "$UPDATER_PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.emailtoprint.updater</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$PROJECT_DIR/update_macos.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/update.log</string>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/update.log</string>
</dict>
</plist>
EOF

echo "🔄 Loading updater launchd service..."
launchctl unload "$UPDATER_PLIST_PATH" 2>/dev/null || true
launchctl load -w "$UPDATER_PLIST_PATH"

echo "🎉 Setup complete! You can check the logs with:"
echo "   tail -f $PROJECT_DIR/output.log"
echo "   tail -f $PROJECT_DIR/error.log"
echo "   tail -f $PROJECT_DIR/update.log"
