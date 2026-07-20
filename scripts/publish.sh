#!/usr/bin/env bash
# Publish gate. Scans tracked files for anything that should never leave the
# repository, then (only with --confirm) wires the remote and pushes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REMOTE="git@github.com:EdgeF-4/agent-control-plane.git"

# Patterns that must never appear in a published file. They live in an
# untracked local file (one grep -E pattern per line) so the list itself is
# never committed; the gate refuses to run without it.
BANNED_FILE="${BANNED_FILE:-$ROOT/.publish-banned}"
if [ ! -f "$BANNED_FILE" ]; then
  echo "Missing $BANNED_FILE (one grep -E pattern per line). Refusing to publish." >&2
  exit 1
fi
BANNED="$(grep -vE '^\s*(#|$)' "$BANNED_FILE" | paste -sd'|' -)"
hits="$(git grep -nIE "$BANNED" -- . ':!scripts/publish.sh' 2>/dev/null || true)"
if [ -n "$hits" ]; then
  echo "LEAK SCAN FAILED — refusing to publish:" >&2
  echo "$hits" >&2
  exit 1
fi
echo "Leak scan clean."

if [ "${1:-}" != "--confirm" ]; then
  echo "Dry run. Re-run with --confirm to set origin ($REMOTE) and push."
  exit 0
fi

git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REMOTE"
git push -u origin "$(git rev-parse --abbrev-ref HEAD)"
