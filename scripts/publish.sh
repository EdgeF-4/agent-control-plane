#!/usr/bin/env bash
# Fail-closed public release gate. It checks the worktree and all reachable Git
# history before allowing an explicitly confirmed non-default branch push.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DENY_FILE="${PUBLIC_RELEASE_DENY_FILE:-$ROOT/.public-release-deny-patterns}"
mode="${1:---check}"
if [ "$#" -gt 1 ] || { [ "$mode" != "--check" ] && [ "$mode" != "--confirm" ]; }; then
  echo "Invalid publish arguments: $*. Next: run 'scripts/publish.sh --check' for a local check or 'scripts/publish.sh --confirm' to push the current non-default branch." >&2
  exit 2
fi

if python3 scripts/public_release_check.py --deny-file "$DENY_FILE"; then
  :
else
  status=$?
  echo "Release check failed with exit $status. Follow the scanner's Next instruction, then rerun 'scripts/publish.sh $mode'." >&2
  exit "$status"
fi

if [ "$mode" = "--check" ]; then
  echo "Release check passed. No network action taken."
  exit 0
fi

if ! branch="$(git symbolic-ref --quiet --short HEAD)"; then
  echo "Cannot determine the current branch because the checkout is detached. Next: run 'git switch agent/<review-name>', then rerun 'scripts/publish.sh --confirm'." >&2
  exit 2
fi
if ! git remote get-url origin >/dev/null; then
  echo "The origin remote is missing or unreadable. Next: run 'git remote add origin <repository-url>' or correct it with 'git remote set-url origin <repository-url>', verify 'git remote -v', then rerun 'scripts/publish.sh --confirm'." >&2
  exit 2
fi
if ! default="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD)"; then
  echo "Cannot determine the default branch because origin/HEAD is unavailable. Next: run 'git remote set-head origin --auto', verify 'git symbolic-ref refs/remotes/origin/HEAD', then rerun 'scripts/publish.sh --confirm'." >&2
  exit 2
fi
default="${default#origin/}"
if [ "$branch" = "$default" ]; then
  echo "Refusing to push the default branch '$default'. Next: run 'git switch -c agent/<review-name>', commit the reviewed changes there, then rerun 'scripts/publish.sh --confirm'." >&2
  exit 2
fi

if git push --set-upstream origin "$branch"; then
  exit 0
else
  status=$?
  echo "Push failed with exit $status. The raw Git failure is above. Next: correct the reported authentication, connectivity, or branch-policy error, verify 'git remote -v', then rerun 'scripts/publish.sh --confirm'." >&2
  exit "$status"
fi
