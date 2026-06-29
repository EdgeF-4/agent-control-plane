#!/usr/bin/env bash
# Publish gate. Scans tracked files for anything that should never leave the
# repository, then (only with --confirm) wires the remote and pushes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
REMOTE="git@github.com:EdgeF-4/agent-control-plane.git"

# Patterns that must never appear in a published file. This script excludes
# itself from the scan, since it necessarily names the patterns it looks for.
BANNED='45\.67\.217|nip\.io|pocketbase|hermes|\bkimi\b|codex|anthropic|\bclaude\b|openai|\bgpt-[0-9]|@gmail\.com'
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
