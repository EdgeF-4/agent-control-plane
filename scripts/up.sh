#!/usr/bin/env bash
# Bring up the full stack. Reads Postgres credentials from config.json so they
# match what the backend uses, without writing secrets to any committed file.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f config.json ]; then
  echo "No config.json. Next: copy config.example.json to config.json, set secrets, run 'chmod 600 config.json', then rerun 'make up'." >&2
  exit 1
fi

if ! python3 -m json.tool config.json >/dev/null; then
  echo "Invalid config.json. Run 'python3 -m json.tool config.json', correct the reported syntax, then retry 'make up'." >&2
  exit 1
fi

if bash scripts/vendor-engines.sh; then
  :
else
  status=$?
  echo "Engine preparation failed with exit $status. Follow its Next instruction, then rerun 'make up'." >&2
  exit "$status"
fi

read_cfg() { python3 -c "import json,sys;print(json.load(open('config.json'))['database'][sys.argv[1]])" "$1"; }
if POSTGRES_USER="$(read_cfg user)"; then
  :
else
  status=$?
  echo "Could not read database.user from config.json. The raw configuration failure is above. Next: add database.user using config.example.json, then rerun 'make up'." >&2
  exit "$status"
fi
export POSTGRES_USER
if POSTGRES_PASSWORD="$(read_cfg password)"; then
  :
else
  status=$?
  echo "Could not read database.password from config.json. The raw configuration failure is above. Next: add database.password using config.example.json, then rerun 'make up'." >&2
  exit "$status"
fi
export POSTGRES_PASSWORD
if POSTGRES_DB="$(read_cfg name)"; then
  :
else
  status=$?
  echo "Could not read database.name from config.json. The raw configuration failure is above. Next: add database.name using config.example.json, then rerun 'make up'." >&2
  exit "$status"
fi
export POSTGRES_DB

if docker compose up --build --detach "$@"; then
  exit 0
else
  status=$?
  echo "Stack startup failed with exit $status. The raw container failure is above. Next: correct the first reported build, port, daemon, or service error, then rerun 'make up'." >&2
  exit "$status"
fi
