#!/usr/bin/env bash
# Local release audit. This script never configures a remote or pushes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ -n "${1:-}" ]; then
  echo "usage: scripts/publish.sh" >&2
  exit 2
fi

echo "==> Checking patch whitespace"
git diff --check

echo "==> Confirming secret-shaped files are not tracked"
tracked_secrets="$(git ls-files | grep -E '(^|/)(config\.json|\.env)$|\.pem$|\.key$|\.p12$' || true)"
if [ -n "$tracked_secrets" ]; then
  echo "secret-shaped tracked files found; refusing release audit" >&2
  exit 1
fi

echo "==> Running backend tests"
make test

echo "==> Building dashboard"
(cd frontend && npm run build)

echo "Local release audit passed. Nothing was published."
