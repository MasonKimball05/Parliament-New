#!/usr/bin/env bash
# pre-push hook (v3.14.1) — refuse to push if the sqlite test suite fails.
#
# Install:  make hooks     (copies this to .git/hooks/pre-push)
# Bypass:   git push --no-verify   (for emergencies — CI still gates the merge)
#
# Rationale: prod deploys are `git pull` of main, so an untested push is one
# `git pull` away from prod. CI (postgres) remains the real gate; this just
# catches breakage before it leaves the machine.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "[pre-push] running sqlite test suite (git push --no-verify to skip)…"
if ! DB_BACKEND=sqlite python3 manage.py test src -v 0; then
  echo ""
  echo "[pre-push] ✗ tests failed — push aborted."
  echo "[pre-push]   fix the failures, or 'git push --no-verify' if you must."
  exit 1
fi
echo "[pre-push] ✓ tests green — pushing."
