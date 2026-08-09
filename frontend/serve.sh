#!/usr/bin/env bash
# Serve the frontend for local development.
#
#   ./serve.sh          # http://localhost:5173
#   PORT=8080 ./serve.sh
#
# The backend runs separately on :8000; the app finds it automatically and
# CORS_ORIGINS=* in the backend's .env lets the two talk.

set -euo pipefail
unset PYTHONPATH  # see backend/scripts/test.sh for why

cd "$(dirname "${BASH_SOURCE[0]}")"
PORT="${PORT:-5173}"

echo "==> Frontend on http://localhost:${PORT}"
echo "    citizen app   http://localhost:${PORT}/index.html"
echo "    control room  http://localhost:${PORT}/control.html"
echo "    backend       expected on http://localhost:8000"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
