#!/usr/bin/env bash
# Copy the five engine packages into backend/engines_vendor/ for an image build.
# This is a build step, not source vendoring. The directory is gitignored, and
# the control plane keeps composing the engines through their public interfaces.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${ENGINES_SRC:-$ROOT/..}"
DEST="$ROOT/backend/engines_vendor"
ENGINES=(cost-governor mcp-gateway mcp-siem-bridge agent-flight-recorder agent-eval)

for engine in "${ENGINES[@]}"; do
  if [ ! -f "$SRC/$engine/pyproject.toml" ]; then
    echo "Missing engine source directory or pyproject.toml: $SRC/$engine. Next: obtain that source, run 'export ENGINES_SRC=/path/to/engine-checkouts', then rerun 'make up'." >&2
    exit 1
  fi
done

if ! command -v rsync >/dev/null; then
  echo "The rsync command is unavailable. Next: install rsync with the host package manager, then rerun 'make up'." >&2
  exit 1
fi
if rm -rf "$DEST"; then
  :
else
  status=$?
  echo "Could not replace the ignored engine build directory at $DEST. The raw removal failure is above. Next: correct its ownership or permissions, then rerun 'make up'." >&2
  exit "$status"
fi
if mkdir -p "$DEST"; then
  :
else
  status=$?
  echo "Could not create the ignored engine build directory at $DEST. The raw filesystem failure is above. Next: correct the parent permissions or free space, then rerun 'make up'." >&2
  exit "$status"
fi
for engine in "${ENGINES[@]}"; do
  if rsync -a \
    --exclude '.git' --exclude '__pycache__' --exclude '.venv' \
    --exclude '*.egg-info' --exclude 'data' --exclude '*.db' --exclude 'runs' \
    "$SRC/$engine/" "$DEST/$engine/"; then
    :
  else
    status=$?
    echo "Copying $engine failed with exit $status. The raw rsync failure is above. Next: correct the reported source, permission, or free-space problem, then rerun 'make up'." >&2
    exit "$status"
  fi
done
echo "vendored ${#ENGINES[@]} engines into $DEST"
