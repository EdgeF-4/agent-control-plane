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
  echo "Missing required engine source directories under: $SRC. Next: obtain every directory listed below, run 'export ENGINES_SRC=/path/to/engine-checkouts', then rerun 'make dev'." >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 2
fi

if python3 -m venv .venv; then
  :
else
  status=$?
  echo "Virtual-environment creation failed with exit $status. The raw Python failure is above. Next: install the Python venv support named by that failure, then rerun 'make dev'." >&2
  exit "$status"
fi
. .venv/bin/activate
if pip install -U pip setuptools wheel; then
  :
else
  status=$?
  echo "Build-tool installation failed with exit $status. The raw package-manager failure is above. Next: correct the reported registry, certificate, or interpreter problem, then rerun 'make dev'." >&2
  exit "$status"
fi
engine_args=()
for engine in "${ENGINES[@]}"; do
  engine_args+=("-e" "$SRC/$engine")
done
if pip install "${engine_args[@]}"; then
  :
else
  status=$?
  echo "Engine installation failed with exit $status. The raw package-manager failure is above. Next: correct the first reported engine build or dependency error, then rerun 'make dev'." >&2
  exit "$status"
fi
if pip install -e "./backend[dev]"; then
  :
else
  status=$?
  echo "Backend installation failed with exit $status. The raw package-manager failure is above. Next: correct the first reported backend build or dependency error, then rerun 'make dev'." >&2
  exit "$status"
fi

echo
echo "Dev environment ready."
echo "  . .venv/bin/activate"
echo "  export ACP_CONFIG=\$PWD/config.local.json"
echo "  python -m app.cli seed && python -m app.cli serve"
