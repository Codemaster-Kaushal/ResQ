#!/usr/bin/env bash
# Smoke-test a deployed instance.
#
#   ./scripts/deploy-check.sh https://rescuenet-backend.onrender.com
#
# Checks the endpoints a judge would actually open, and exits non-zero on the first
# failure so it can be wired into CI.

set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
FAILED=0

check() {
  local label="$1" path="$2" expected="${3:-200}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${BASE}${path}" || echo 000)"
  if [[ "$code" == "$expected" ]]; then
    printf '  ok    %-28s %s\n' "$label" "$code"
  else
    printf '  FAIL  %-28s %s (expected %s)\n' "$label" "$code" "$expected"
    FAILED=1
  fi
}

echo "Checking ${BASE}"
check "health"          /health
check "openapi"         /openapi.json
check "docs"            /docs
check "reports"         "/api/reports?limit=1"
check "queue"           "/api/queue?limit=1"
check "responders"      /api/responders
check "events"          "/api/events?limit=1"
check "csv export"      /api/events/export.csv
check "bottlenecks"     /api/mining/bottlenecks
check "governance"      /api/governance
check "unknown route"   /api/does-not-exist 404

echo
if [[ "$FAILED" -eq 0 ]]; then
  echo "All checks passed."
  curl -s "${BASE}/api/governance" |
    python3 -c 'import json,sys; print("Scoring:", json.load(sys.stdin)["scoring"]["honest_summary"])' \
    2>/dev/null || true
else
  echo "One or more checks failed."
fi

exit "$FAILED"
