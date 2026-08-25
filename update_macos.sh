#!/bin/bash

# Automatic update script for Email to Print Service on MacOS
# This pulls the latest code, syncs dependencies, and restarts the launchd service

set -e

# Ensure PATH includes common installation directories for uv and git
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
PLIST_PATH="$HOME/Library/LaunchAgents/com.emailtoprint.service.plist"

echo "=== Starting update at $(date) ==="

echo "⏬ Pulling latest changes from git..."
git pull origin main || echo "Git pull failed, continuing..."

echo "📦 Syncing dependencies with uv..."
uv sync

echo "🔄 Restarting the launchd service..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "✅ Update complete at $(date)"
