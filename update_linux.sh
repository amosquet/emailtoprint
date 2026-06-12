#!/bin/bash

# Email to Print Service Update Script
# This script pulls the latest code, syncs dependencies, and restarts the service.

set -e

echo "🚀 Starting update for Email to Print service..."

echo "🔍 Checking for updates..."
git fetch

if [ $(git rev-parse HEAD) = $(git rev-parse @{u}) ]; then
    echo "✅ Already up to date. No update needed."
    exit 0
fi

# 1. Pull latest code from git
echo "📥 Pulling latest code from git..."
git pull

# 2. Sync the environment
echo "📦 Syncing environment with uv..."
uv sync

# 3. Restart the service
echo "🔄 Restarting systemd service..."
sudo systemctl restart emailtoprint.service
