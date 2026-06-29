#!/usr/bin/env bash
# Bring up the full stack. Reads Postgres credentials from config.json so they
# match what the backend uses, without writing secrets to any committed file.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f config.json ]; then
  echo "No config.json. Copy config.example.json to config.json, set secrets, chmod 600." >&2
  exit 1
fi

bash scripts/vendor-engines.sh

read_cfg() { python3 -c "import json,sys;print(json.load(open('config.json'))['database'][sys.argv[1]])" "$1"; }
POSTGRES_USER="$(read_cfg user)"; export POSTGRES_USER
POSTGRES_PASSWORD="$(read_cfg password)"; export POSTGRES_PASSWORD
POSTGRES_DB="$(read_cfg name)"; export POSTGRES_DB

exec docker compose up --build "$@"
