#!/usr/bin/env bash
# Local (non-Docker) dev setup: a virtualenv with the engines and backend
# installed editable, against a SQLite store. Use config.local.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SRC="${ENGINES_SRC:-$HOME}"

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip setuptools wheel
pip install \
  -e "$SRC/cost-governor" -e "$SRC/mcp-gateway" -e "$SRC/mcp-siem-bridge" \
  -e "$SRC/agent-flight-recorder" -e "$SRC/agent-eval"
pip install -e "./backend[dev]"

echo
echo "Dev environment ready."
echo "  . .venv/bin/activate"
echo "  export ACP_CONFIG=\$PWD/config.local.json"
echo "  python -m app.cli seed && python -m app.cli serve"
