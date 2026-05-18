#!/usr/bin/env bash
# Deploy fairing code to homeserver without touching runtime data or secrets.
#
# The running container mounts /opt/docker/fairing_git at /fairing. Daily cron
# runs /fairing/main.py, so syncing this tree updates the next scheduled run.

set -euo pipefail

HOST="${FAIRING_DEPLOY_HOST:-homeserver-ext}"
REMOTE_DIR="${FAIRING_REMOTE_DIR:-/opt/docker/fairing_git}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> Syncing fairing code to $HOST:$REMOTE_DIR"
rsync -av \
  --exclude='.git/' \
  --exclude='.env' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.scoring_store.jsonl' \
  --exclude='seen_urls.json' \
  --exclude='scoring_store.jsonl' \
  --exclude='title_index.jsonl' \
  --exclude='rate_pending.json' \
  --exclude='payload_queue.json' \
  --exclude='feed_errors.json' \
  --exclude='last_run_time' \
  --exclude='config/sources.local.yaml' \
  --exclude='config/sources.local.yaml.example' \
  "$SCRIPT_DIR/" "$HOST:$REMOTE_DIR/"

echo "==> Verifying remote Python syntax"
ssh "$HOST" "cd '$REMOTE_DIR' && python3 -m compileall -q fairing main.py"

echo "==> fairing deploy complete"
