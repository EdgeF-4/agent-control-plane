#!/usr/bin/env bash
set -euo pipefail

API_URL="${ACP_API_URL:-http://127.0.0.1:${ACP_API_PORT:-8800}}"
WEB_URL="${ACP_WEB_URL:-http://127.0.0.1:${ACP_WEB_PORT:-8801}}"

health="$(curl --fail --silent --show-error --max-time 10 "$API_URL/healthz")"
python3 -c 'import json,sys; body=json.loads(sys.argv[1]); assert body.get("status") == "ok"' "$health"
echo "PASS api health"

curl --fail --silent --show-error --max-time 10 --output /dev/null "$WEB_URL/"
echo "PASS dashboard"
echo "2 smoke checks passed"
