#!/bin/bash

# Email to Print Service Update Script for Linux
# This script pulls the latest code, syncs dependencies, and restarts the systemd user service.

set -e

# Ensure PATH includes common installation directories for uv and git
export PATH="/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=== Starting update at $(date) ==="
echo "🚀 Checking for Email to Print service updates..."

git fetch

LOCAL=$(git rev-parse HEAD 2>/dev/null || echo "")
REMOTE=$(git rev-parse @{u} 2>/dev/null || echo "")

if [ -n "$LOCAL" ] && [ "$LOCAL" = "$REMOTE" ]; then
    echo "✅ Already up to date. No update needed."
    exit 0
fi

# 1. Pull latest code from git
echo "📥 Pulling latest code from git..."
git pull

# 2. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# 3. Restart the user service
echo "🔄 Restarting systemd user service..."
systemctl --user restart emailtoprint.service

echo "✅ Update complete at $(date)"
