#!/usr/bin/env bash
set -euo pipefail

API_URL="${ACP_API_URL:-http://127.0.0.1:${ACP_API_PORT:-8800}}"
WEB_URL="${ACP_WEB_URL:-http://127.0.0.1:${ACP_WEB_PORT:-8801}}"

if ! health="$(curl --fail --silent --show-error --max-time 10 "$API_URL/healthz" 2>&1)"; then
  echo "API health check failed at $API_URL: $health. Run 'make up', correct ACP_API_URL or ACP_API_PORT, then retry 'make smoke'." >&2
  exit 1
fi
if ! python3 -c 'import json,sys; body=json.loads(sys.argv[1]); raise SystemExit(0 if body.get("status") == "ok" else 1)' "$health"; then
  echo "API health response was not ready. Check 'docker compose logs backend', correct the startup error, then retry 'make smoke'." >&2
  exit 1
fi
echo "PASS api health"

if ! dashboard_error="$(curl --fail --silent --show-error --max-time 10 --output /dev/null "$WEB_URL/" 2>&1)"; then
  echo "Dashboard check failed at $WEB_URL: $dashboard_error. Run 'make up', correct ACP_WEB_URL or ACP_WEB_PORT, then retry 'make smoke'." >&2
  exit 1
fi
echo "PASS dashboard"
echo "2 smoke checks passed"
