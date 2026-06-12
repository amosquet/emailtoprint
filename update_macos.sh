#!/bin/bash

# Automatic update script for Email to Print Service on MacOS
# This pulls the latest code, syncs dependencies, and restarts the launchd service

set -e

PROJECT_DIR="$(pwd)"
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
