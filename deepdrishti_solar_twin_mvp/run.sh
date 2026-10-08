#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo
echo "DeepDrishti Solar Twin is starting at http://127.0.0.1:8000"
echo "Press Ctrl+C to stop."
echo
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
