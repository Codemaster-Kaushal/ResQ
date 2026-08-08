#!/usr/bin/env bash
# Single-command local startup (NFR-6). Creates the venv, installs, and runs the API.
#
#   ./scripts/dev.sh            # http://localhost:8000
#   PORT=9000 ./scripts/dev.sh

set -euo pipefail

# A sourced ROS/cognipilot environment puts its own entries on PYTHONPATH, and Python
# searches those before the venv's site-packages. Drop it so RescueNet only ever imports
# from .venv. This affects this process only; your ROS setup is untouched.
unset PYTHONPATH

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

VENV="${BACKEND_DIR}/.venv"
PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

if [[ ! -d "$VENV" ]]; then
  echo "==> Creating virtualenv"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --upgrade pip --quiet
fi

echo "==> Installing dependencies"
"$VENV/bin/pip" install -r requirements-dev.txt --quiet

if [[ ! -f .env ]]; then
  echo "==> No .env found, copying .env.example"
  cp .env.example .env
fi

echo "==> Starting RescueNet API on http://${HOST}:${PORT} (docs at /docs)"
exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" --reload
