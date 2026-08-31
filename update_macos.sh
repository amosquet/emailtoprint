#!/bin/bash

# Automatic update script for Email to Print Service on MacOS
# This pulls the latest code, syncs dependencies, and restarts the launchd service

set -e

# Ensure PATH includes common installation directories for uv and git
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
SERVICE_LABEL="com.emailtoprint.service"
PLIST_PATH="$HOME/Library/LaunchAgents/$SERVICE_LABEL.plist"

echo "=== Starting update at $(date) ==="
echo "🚀 Checking for Email to Print service updates..."

git fetch

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")

if [ -n "$LOCAL" ] && [ "$LOCAL" = "$REMOTE" ]; then
    echo "✅ Already up to date. No update needed."
    exit 0
fi

echo "⏬ Pulling latest changes from git..."
git pull

echo "📦 Syncing dependencies with uv..."
uv sync

echo "🔄 Restarting the launchd service..."
launchctl kickstart -k "gui/$(id -u)/$SERVICE_LABEL" 2>/dev/null || {
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    launchctl load -w "$PLIST_PATH"
}

echo "✅ Update complete at $(date)"
