#!/usr/bin/env bash
# Fail-closed public release gate. It checks the worktree and all reachable Git
# history before allowing an explicitly confirmed non-default branch push.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DENY_FILE="${PUBLIC_RELEASE_DENY_FILE:-$ROOT/.public-release-deny-patterns}"
python3 scripts/public_release_check.py --deny-file "$DENY_FILE"

if [ "${1:---check}" = "--check" ]; then
  echo "Release check passed. No network action taken."
  exit 0
fi
if [ "$1" != "--confirm" ]; then
  echo "Usage: scripts/publish.sh [--check|--confirm]" >&2
  exit 2
fi

branch="$(git symbolic-ref --quiet --short HEAD)"
default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD)"
default="${default#origin/}"
if [ -z "$branch" ] || [ -z "$default" ]; then
  echo "Cannot determine current and default branches. Refusing to push." >&2
  exit 2
fi
if [ "$branch" = "$default" ]; then
  echo "Refusing to push the default branch." >&2
  exit 2
fi

git remote get-url origin >/dev/null
git push --set-upstream origin "$branch"
