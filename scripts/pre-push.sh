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
# ⚠️ v3.19.9 — THIS HOOK WAS WRITTEN ON 07-18-26 AND WAS NEVER INSTALLED.
# `.git/hooks/` contained nothing but Git's own `.sample` files. So the guard
# existed, was argued for, was committed — and its trigger was a one-time manual
# `make hooks` that nothing verified.
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

# ---------------------------------------------------------------------------
# Find an interpreter that can actually import Django.
# ---------------------------------------------------------------------------
# ⚠️ v3.19.9 — THE FIRST VERSION OF THIS HOOK CALLED BARE `python3` AND BLOCKED
# THE FIRST PUSH IT EVER SAW. A git hook runs in a **non-interactive, non-login
# shell with no virtualenv activated**, so `python3` is whatever the system
# ships — on macOS, an interpreter with no Django in it. The hook reported
# "tests failed" for an `ImportError` that had nothing to do with the tests.
#
# That is worth more than a one-line fix, because it is the same defect class
# the hook exists to catch: **a check that cannot run reports the same way as a
# check that failed.** A guard whose failure mode is indistinguishable from the
# thing it guards against is a guard people delete — which is, precisely, how
# this repo went a month with no hook installed.
#
# So: resolve the interpreter deliberately, and treat "no usable interpreter" as
# a DIFFERENT outcome from "tests failed" — see the block below.
PY=''
for candidate in \
    "${PARLIAMENT_PYTHON:-}" \
    "${VIRTUAL_ENV:+$VIRTUAL_ENV/bin/python}" \
    ".venv/bin/python" \
    "venv/bin/python" \
    "env/bin/python" \
    "$(command -v python3 || true)"
do
  [ -n "$candidate" ] || continue
  [ -x "$candidate" ] || continue
  if "$candidate" -c 'import django' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "[pre-push] ⚠️  SKIPPED — no interpreter on this machine can import Django."
  echo "[pre-push]     Looked at: \$PARLIAMENT_PYTHON, \$VIRTUAL_ENV/bin/python,"
  echo "[pre-push]     .venv/bin/python, venv/bin/python, env/bin/python, python3."
  echo "[pre-push]"
  echo "[pre-push]     The push is ALLOWED, deliberately: a hook that blocks every"
  echo "[pre-push]     push because of an environment problem gets deleted, and a"
  echo "[pre-push]     deleted hook is why this repo went a month without one."
  echo "[pre-push]     But nothing was checked — CI is now the only gate."
  echo "[pre-push]"
  echo "[pre-push]     Fix by creating .venv, or: export PARLIAMENT_PYTHON=/path/to/python"
  echo ""
  exit 0
fi

echo "[pre-push] using $PY"
echo "[pre-push] running sqlite test suite (git push --no-verify to skip)…"

# ⚠️ v3.19.9 — THE OUTPUT IS TEED AND RE-SUMMARISED, and that is not polish.
# Several tests in this suite print progress to stdout, so a `FAIL:` line lands
# hundreds of lines above the hook's own message and the last thing on screen is
# some unrelated test's debug output. The first failure this hook reported was
# read as "the vote-summary test broke" for exactly that reason. **A gate that
# says "something failed" without saying what gets bypassed rather than acted
# on.** So on failure the hook replays just the verdict lines.
# Portable form: macOS `mktemp -t X` and GNU `mktemp -t` disagree about what
# the argument means, and the hook has to run on both.
_log="$(mktemp "${TMPDIR:-/tmp}/parliament-prepush.XXXXXX")"
if ! DB_BACKEND=sqlite "$PY" manage.py test src -v 0 2>&1 | tee "$_log"; then
  echo ""
  echo "[pre-push] ─────────────────────────────────────────────────────────"
  echo "[pre-push] ✗ tests failed — push aborted. What failed:"
  echo ""
  grep -E '^(FAIL|ERROR):|^Ran [0-9]+ test|^(FAILED|OK)\b' "$_log" | sed 's/^/    /'
  echo ""
  echo "[pre-push]   Full output: $_log"
  echo "[pre-push]   Re-run:      DB_BACKEND=sqlite $PY manage.py test src"
  echo "[pre-push]   Or bypass:   git push --no-verify"
  echo "[pre-push] ─────────────────────────────────────────────────────────"
  exit 1
fi
rm -f "$_log"
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
if ! DB_BACKEND=sqlite "$PY" - <<'PY'
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
  echo "[pre-push]   'not committed'. Both want the sha printed by:"
  echo "[pre-push]     git log --diff-filter=A --format=%h -- changelogs/<file>"
  echo "[pre-push]"
  echo "[pre-push]   ⚠️  Fix with a FOLLOW-UP COMMIT, not 'git commit --amend'."
  echo "[pre-push]   Amending rewrites the commit, which changes the very sha you"
  echo "[pre-push]   just wrote down — and src.W003 also checks that the recorded"
  echo "[pre-push]   sha MATCHES, so an amend trades one red check for another."
  exit 1
fi
echo "[pre-push] ✓ release ledger current — pushing."
