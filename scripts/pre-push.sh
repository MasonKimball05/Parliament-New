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
echo "[pre-push] running sqlite test suite in parallel (git push --no-verify to skip)…"

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
# ⚠️ v3.21.4 — `--parallel`, because a gate people wait several minutes for is
# a gate people learn to `--no-verify` past. Measured on this suite: ~291 s
# serial, ~130 s across 8 workers. `tblib` has been a dependency since v3.19.9
# precisely so that a parallel failure still reports which test failed — without
# it the run aborts with `TypeError: cannot pickle 'traceback' object` and no
# results at all, which is worse than slow.
#
# `--parallel` with no value uses one worker per core. It is deliberately not
# pinned: this runs on whatever laptop is pushing.
if ! DB_BACKEND=sqlite "$PY" manage.py test src -v 0 --parallel 2>&1 | tee "$_log"; then
  # ⚠️ v3.21.5 — A PARALLEL RUN CAN FAIL WITHOUT REPORTING ANY TEST.
  # Django's parallel runner ships each failure to the parent by pickling it.
  # `tblib` (a dependency since v3.19.9) makes tracebacks picklable, and that
  # is genuinely necessary — but it is not sufficient: on a badly-red tree the
  # payload can still contain something the pool cannot pickle, and the run
  # then dies with `multiprocessing.pool.MaybeEncodingError` having reported
  # **zero** results. Reproduced deterministically on 08-20-26.
  #
  # v3.21.4's note says tblib exists "precisely so that a parallel failure
  # still reports which test failed". Usually it does. When it does not, the
  # summary line is absent — which is a signal, so use it rather than printing
  # an empty "What failed:" list and leaving the reader with a stack trace from
  # inside `multiprocessing`.
  #
  # **A gate that cannot say what failed is, at that moment, a gate that did
  # not run** — the same rule as the missing-interpreter branch above. So the
  # answer is to go and get the answer, not to report a blank one.
  if ! grep -qE '^Ran [0-9]+ test' "$_log"; then
    echo ""
    echo "[pre-push] ⚠️  the parallel run aborted without reporting any test."
    echo "[pre-push]     Re-running SERIALLY to find out what actually failed."
    echo "[pre-push]     (This is slower. It only happens on a red tree.)"
    echo ""
    DB_BACKEND=sqlite "$PY" manage.py test src -v 0 2>&1 | tee "$_log" || true
  fi

  echo ""
  echo "[pre-push] ─────────────────────────────────────────────────────────"
  echo "[pre-push] ✗ tests failed — push aborted. What failed:"
  echo ""
  grep -E '^(FAIL|ERROR):|^Ran [0-9]+ test|^(FAILED|OK)\b' "$_log" | sed 's/^/    /'
  echo ""
  echo "[pre-push]   Full output: $_log"
  echo "[pre-push]   Re-run:      DB_BACKEND=sqlite $PY manage.py test src --parallel"
  echo "[pre-push]   If a failure is unclear, re-run SERIALLY — parallel workers"
  echo "[pre-push]   interleave output:  DB_BACKEND=sqlite $PY manage.py test src"
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
  echo "[pre-push]   Fix it with one command:"
  echo "[pre-push]"
  echo "[pre-push]     make stamp-ledger"
  echo "[pre-push]     git add changelogs/ && git commit -m 'Stamp release ledger'"
  echo "[pre-push]"
  echo "[pre-push]   It fills both lines from git log --diff-filter=A, the same"
  echo "[pre-push]   source this check reads, and never touches the 'Deployed'"
  echo "[pre-push]   column — git cannot know that, only you can."
  echo "[pre-push]"
  echo "[pre-push]   ⚠️  Fix with a FOLLOW-UP COMMIT, not 'git commit --amend'."
  echo "[pre-push]   Amending rewrites the commit, which changes the very sha you"
  echo "[pre-push]   just wrote down — and src.W003 also checks that the recorded"
  echo "[pre-push]   sha MATCHES, so an amend trades one red check for another."
  exit 1
fi
echo "[pre-push] ✓ release ledger current."

# ---------------------------------------------------------------------------
# The two security scans CI runs — v3.19.10
# ---------------------------------------------------------------------------
# ⚠️ WHY THESE ARE HERE. CI's `security` job runs bandit and pip-audit on every
# push to main, neither step carrying `continue-on-error`. **The bandit step
# exited 1 continuously from 07-29-26 to 08-17-26** — nineteen days, roughly a
# dozen pushes, six of them releases whose whole subject was code quality.
# Nothing swallowed the signal: GitHub rendered a red ❌ every single time. The
# entire failure was in the reading.
#
# That is the cheapest of this repo's four recorded "red gate nobody read"
# incidents to have caught, and the one that lasted longest — which is the
# argument for moving the signal to where someone is already looking. The whole
# reason this hook exists is that a check whose trigger is "somebody remembers"
# is not triggered.
#
# ⚠️ AND NOTE WHAT DID NOT CATCH IT: this hook, built in v3.19.9 for exactly
# that pattern, ran the suite and the ledger checks and not these. **A trigger
# built in response to a pattern still has to be pointed at every instance of
# it.**
#
# Both follow the rule established at the top of this file — **a check that
# cannot run must not report like a check that failed.** A missing binary or a
# network the laptop does not have is a LOUD SKIP that allows the push, never an
# abort. Verified by running both branches.
#
# ⚠️ AND THE SUMMARY LINE HAS TO SAY WHICH. The first draft of this block ended
# with "✓ all gates green" after skipping both scans, which is the same defect
# in its mirror image: a check that cannot run must not report like a check that
# *passed* either. `_skipped` exists so the last line on screen — the only line
# most pushes will be read for — distinguishes "checked and clean" from "not
# checked". Caught by running the missing-tool branch rather than reasoning
# about it.
_skipped=0

_scan_tool() {
  # Echo a runnable command for $1, preferring the resolved interpreter's module
  # form so the tool matches the environment being pushed. Silent on failure —
  # the caller decides what "not found" means.
  if "$PY" -m "$2" --version >/dev/null 2>&1; then
    echo "$PY -m $2"
  elif command -v "$1" >/dev/null 2>&1; then
    command -v "$1"
  fi
}

echo "[pre-push] running the security scans CI runs…"

# ── bandit ────────────────────────────────────────────────────────────────
# Flags match .github/workflows/ci.yml exactly. If they ever diverge, this hook
# starts passing pushes CI rejects, which is worse than not running it.
_bandit="$(_scan_tool bandit bandit)"
if [ -z "$_bandit" ]; then
  echo "[pre-push] ⚠️  bandit not installed — SKIPPED, push allowed."
  echo "[pre-push]     Install with: $PY -m pip install bandit"
  echo "[pre-push]     CI still runs it, and CI is the gate that matters."
  _skipped=1
else
  _bout="$(mktemp "${TMPDIR:-/tmp}/parliament-bandit.XXXXXX")"
  # bandit exits 1 for findings and non-1 for its own errors, so the exit code
  # alone cannot tell "12 MEDIUM findings" from "bandit crashed". The JSON
  # either parses or it does not, and that distinction is the one that matters.
  $_bandit -r src/ -ll --exclude src/migrations -f json -q >"$_bout" 2>/dev/null
  _verdict="$("$PY" - "$_bout" <<'PY' 2>/dev/null
import json, sys
try:
    results = json.load(open(sys.argv[1]))['results']
except Exception:
    sys.exit(0)                      # unparseable -> could not run
hits = [r for r in results if r['issue_severity'] in ('MEDIUM', 'HIGH')]
print('CLEAN' if not hits else 'FINDINGS')
for r in hits[:15]:
    print("    {issue_severity:6} {test_id} {filename}:{line_number}  {issue_text}".format(**r)[:150])
PY
)"
  case "$_verdict" in
    CLEAN*)
      echo "[pre-push] ✓ bandit clean."
      ;;
    FINDINGS*)
      echo ""
      echo "[pre-push] ─────────────────────────────────────────────────────────"
      echo "[pre-push] ✗ bandit found MEDIUM+ issues — push aborted. CI will fail."
      echo ""
      printf '%s\n' "$_verdict" | tail -n +2
      echo ""
      echo "[pre-push]   Re-run:  $_bandit -r src/ -ll --exclude src/migrations"
      echo "[pre-push]   If a finding is traced-safe, add an inline justified"
      echo "[pre-push]   '# nosec BXXX - why' — do NOT downgrade the gate."
      echo "[pre-push]   Or bypass: git push --no-verify"
      echo "[pre-push] ─────────────────────────────────────────────────────────"
      rm -f "$_bout"
      exit 1
      ;;
    *)
      echo "[pre-push] ⚠️  bandit produced no parseable output — SKIPPED, push allowed."
      echo "[pre-push]     Nothing was checked. Output kept at: $_bout"
      _skipped=1
      ;;
  esac
  rm -f "$_bout" 2>/dev/null || true
fi

# ── pip-audit ─────────────────────────────────────────────────────────────
# ⚠️ THIS AUDITS `requirements.txt`, WHILE CI AUDITS THE INSTALLED ENVIRONMENT,
# and the difference is deliberate. A developer's venv accumulates tools the
# server never sees, so auditing it here would block pushes on CVEs that CI
# cannot see — and a gate that blocks for reasons the build does not share is a
# gate people learn to bypass. The residual gap is a CVE in bandit or pip-audit
# themselves, which CI would catch and this will not. That is the right way
# round: **prefer the false negative in the local gate and the false positive in
# the remote one.**
#
# `--ignore-vuln PYSEC-2025-49` mirrors CI (setuptools PackageIndex path
# traversal — easy_install-era build tooling, never imported at runtime).
_audit="$(_scan_tool pip-audit pip_audit)"
if [ -z "$_audit" ]; then
  echo "[pre-push] ⚠️  pip-audit not installed — SKIPPED, push allowed."
  echo "[pre-push]     Install with: $PY -m pip install pip-audit"
  _skipped=1
else
  _aout="$(mktemp "${TMPDIR:-/tmp}/parliament-audit.XXXXXX")"
  $_audit -r requirements.txt --ignore-vuln PYSEC-2025-49 \
          --format json --progress-spinner off >"$_aout" 2>/dev/null
  _verdict="$("$PY" - "$_aout" <<'PY' 2>/dev/null
import json, sys
try:
    deps = json.load(open(sys.argv[1]))['dependencies']
except Exception:
    sys.exit(0)                      # no network, or pip-audit errored out
hits = [(d['name'], d['version'], v['id'], ','.join(v.get('fix_versions') or ['-']))
        for d in deps for v in d.get('vulns', [])]
print('CLEAN' if not hits else 'FINDINGS')
for name, version, vid, fix in hits[:15]:
    print(f'    {name} {version}  {vid}  -> fix {fix}')
PY
)"
  case "$_verdict" in
    CLEAN*)
      echo "[pre-push] ✓ pip-audit clean."
      ;;
    FINDINGS*)
      echo ""
      echo "[pre-push] ─────────────────────────────────────────────────────────"
      echo "[pre-push] ✗ known CVEs in pinned dependencies — push aborted. CI will fail."
      echo ""
      printf '%s\n' "$_verdict" | tail -n +2
      echo ""
      echo "[pre-push]   Bump the pin and re-run the suite BEFORE pushing — a"
      echo "[pre-push]   dependency bump is a code change with no diff."
      echo "[pre-push]   Check what it drags with it: pyOpenSSL pins cryptography."
      echo "[pre-push]   Or bypass: git push --no-verify"
      echo "[pre-push] ─────────────────────────────────────────────────────────"
      rm -f "$_aout"
      exit 1
      ;;
    *)
      echo "[pre-push] ⚠️  pip-audit could not report — SKIPPED, push allowed."
      echo "[pre-push]     Usually no network. Nothing was checked; CI will run it."
      _skipped=1
      ;;
  esac
  rm -f "$_aout" 2>/dev/null || true
fi

if [ "$_skipped" -eq 0 ]; then
  echo "[pre-push] ✓ all gates green — pushing."
else
  echo "[pre-push] ⚠️  pushing with one or more scans SKIPPED (see above)."
  echo "[pre-push]     The suite and the ledger WERE checked; the security scans"
  echo "[pre-push]     were not. CI is the only thing standing behind them now."
fi
