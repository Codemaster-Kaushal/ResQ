#!/usr/bin/env bash
# Load the demo dataset.
#
#   ./scripts/seed.sh            # insert anything missing (safe to re-run)
#   ./scripts/seed.sh --reset    # wipe seeded data first

set -euo pipefail

# See scripts/test.sh for why: a sourced ROS environment precedes the venv on sys.path.
unset PYTHONPATH

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

exec "${BACKEND_DIR}/.venv/bin/python" -m seed.seed "$@"
