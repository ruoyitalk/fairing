#!/usr/bin/env bash
# Usage:
#   bash run.sh                              # interactive shell
#   bash run.sh run                          # non-interactive default run
#   bash run.sh run --md                     # Obsidian only (mode)
#   bash run.sh run --no-mail                # modifier
#   bash run.sh run --chinese                # modifier
#   bash run.sh run --fulltext               # modifier
#   bash run.sh run --all                    # --chinese + --fulltext (exclusive combo)
#   bash run.sh run --all --no-mail          # valid combination
#   bash run.sh run --force                  # bypass rate gate
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
