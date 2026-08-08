#!/usr/bin/env bash
# Run the test suite.
#
#   ./scripts/test.sh                 # everything
#   ./scripts/test.sh tests/test_errors.py -v

set -euo pipefail

# pytest auto-loads plugins from every importable path. A sourced ROS environment
# leaves its own packages on PYTHONPATH, and pytest then tries to load ROS's
# `launch_testing` plugin, which fails on an unrelated missing dependency. Dropping
# PYTHONPATH keeps the suite reading only from .venv.
unset PYTHONPATH

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

exec "${BACKEND_DIR}/.venv/bin/pytest" "$@"
