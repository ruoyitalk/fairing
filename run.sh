#!/usr/bin/env bash
# Usage:
#   bash run.sh           # start interactive shell
#   bash run.sh run       # run digest non-interactively
#   bash run.sh run --chinese
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

if [ ! -f "$PYTHON" ]; then
  echo "[setup] Creating virtual environment..."
  python3 -m venv "$VENV_DIR"
fi

echo "[setup] Checking dependencies..."
"$PYTHON" -m pip install -q -r "$SCRIPT_DIR/requirements.txt"

cd "$SCRIPT_DIR"
"$PYTHON" main.py "$@"
