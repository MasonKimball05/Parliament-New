#!/usr/bin/env bash
# pre-push hook (v3.14.1, extended v3.19.9) — refuse to push a red tree.
#
# Install:  make hooks     (copies this to .git/hooks/pre-push)
# Bypass:   git push --no-verify   (for emergencies — CI still gates the merge)
#
# Rationale: prod deploys are `git pull` of main, so an untested push is one
# `git pull` away from prod. CI (postgres) remains the real gate; this just
# catches breakage before it leaves the machine.
#
# ⚠️ v3.19.9 — THIS HOOK WAS WRITTEN ON 07-18-26 AND HAS NEVER BEEN INSTALLED.
# `.git/hooks/` contains nothing but Git's own `.sample` files, and
# `core.hooksPath` is unset. So the guard existed, was argued for, was committed
# — and its trigger was a one-time manual `make hooks` that nothing verified.
#
# The cost is measurable in this repo's own history:
#   * `test_url_smoke` was red from 07-30 to 08-02 across several pushes;
#   * v3.19.6 found 12 pre-existing failures on an already-pushed commit;
#   * seven consecutive releases were pushed with a stale ledger line.
# Every one of those is a thing this hook refuses to push.
#
# `src/test_repo_hooks.py` now fails the suite when the hook is missing or out
# of date, so the absence is visible from inside the thing it guards. That is
# weaker than a hook that installs itself and it is what is available: a hook is
# local configuration, it cannot be delivered by a commit.
set -uo pipefail

cd "$(git rev-parse --show-toplevel)"

echo "[pre-push] running sqlite test suite (git push --no-verify to skip)…"
if ! DB_BACKEND=sqlite python3 manage.py test src -v 0; then
  echo ""
  echo "[pre-push] ✗ tests failed — push aborted."
  echo "[pre-push]   fix the failures, or 'git push --no-verify' if you must."
  exit 1
fi
echo "[pre-push] ✓ tests green."

# v3.19.9 — the release-integrity system checks, at the only moment they can be
# fixed cheaply.
#
# `src.W003` exists because a changelog's "**Committed & pushed:**" line and its
# DEPLOYED.md row record facts that do not exist until the commit is made, so
# they are always written stale. v3.19.8 put the check in `manage.py preflight`
# — correct, and preflight runs on the server at deploy time, by which point the
# stale line is already in history and needs a follow-up commit. Here it is
# still an `--amend`.
#
# The gating set is imported from preflight rather than repeated: one
# definition, two triggers.
echo "[pre-push] checking the release ledger and schema gates…"
if ! DB_BACKEND=sqlite python3 - <<'PY'
import os, sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings')
import django
django.setup()

from django.core import checks
from src.management.commands.preflight import RELEASE_GATING_CHECK_IDS

blocking = [
    m for m in checks.run_checks()
    if m.is_serious(checks.ERROR) or m.id in RELEASE_GATING_CHECK_IDS
]
for m in blocking:
    print(f'  {m.id or "check"}: {str(m.msg).splitlines()[0]}')
sys.exit(1 if blocking else 0)
PY
then
  echo ""
  echo "[pre-push] ✗ release-integrity check failed — push aborted."
  echo "[pre-push]   usually: a changelog still says 'not yet' under"
  echo "[pre-push]   '**Committed & pushed:**', or its DEPLOYED.md row says"
  echo "[pre-push]   'not committed'. Fix both and 'git commit --amend'."
  exit 1
fi
echo "[pre-push] ✓ release ledger current — pushing."
