#!/usr/bin/env bash
# Copy the five engine packages into backend/engines_vendor/ for an image build.
# This is a build step, not source vendoring — the directory is gitignored, and
# the control plane keeps composing the engines through their public interfaces.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ENGINES_SRC:-$HOME}"
DEST="$ROOT/backend/engines_vendor"
ENGINES=(cost-governor mcp-gateway mcp-siem-bridge agent-flight-recorder agent-eval)

rm -rf "$DEST"
mkdir -p "$DEST"
for engine in "${ENGINES[@]}"; do
  if [ ! -d "$SRC/$engine" ]; then
    echo "missing engine source: $SRC/$engine (set ENGINES_SRC)" >&2
    exit 1
  fi
  rsync -a \
    --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
    --exclude '*.egg-info' --exclude 'data' --exclude '*.db' --exclude 'runs' \
    "$SRC/$engine/" "$DEST/$engine/"
done
echo "vendored ${#ENGINES[@]} engines into $DEST"
