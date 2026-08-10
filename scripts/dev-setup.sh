#!/usr/bin/env bash
# Local (non-Docker) dev setup: a virtualenv with the engines and backend
# installed editable, against a SQLite store. Use config.local.json.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SRC="${ENGINES_SRC:-$ROOT/..}"
ENGINES=(cost-governor mcp-gateway mcp-siem-bridge agent-flight-recorder agent-eval)

missing=()
for engine in "${ENGINES[@]}"; do
  [ -f "$SRC/$engine/pyproject.toml" ] || missing+=("$engine")
done
if [ "${#missing[@]}" -ne 0 ]; then
  echo "Missing required engine source directories under: $SRC" >&2
  printf '  %s\n' "${missing[@]}" >&2
  echo "Set ENGINES_SRC to their parent directory. See README.md." >&2
  exit 2
fi

python3 -m venv .venv
. .venv/bin/activate
pip install -U pip setuptools wheel
engine_args=()
for engine in "${ENGINES[@]}"; do
  engine_args+=("-e" "$SRC/$engine")
done
pip install "${engine_args[@]}"
pip install -e "./backend[dev]"

echo
echo "Dev environment ready."
echo "  . .venv/bin/activate"
echo "  export ACP_CONFIG=\$PWD/config.local.json"
echo "  python -m app.cli seed && python -m app.cli serve"
