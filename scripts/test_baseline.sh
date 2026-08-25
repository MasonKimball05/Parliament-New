#!/usr/bin/env bash
#
# Per-module test baseline.
#
# WHY THIS EXISTS
# ---------------
# Three consecutive auto-run reports disagreed about how many tests are red:
# 07-29 measured "~22-25 failures across five modules", v3.17.4's notes claimed
# "786 green across all 37 modules", and 07-30 and 07-31 both had to report the
# question as unresolved. The reason is boring and fixable: `manage.py test`
# over the whole suite takes long enough that it gets reaped, and when it dies
# you learn nothing at all — not even which module it died in.
#
# Running each module as its own process fixes that. A module that hangs costs
# you that module, not the run. And the output is a table you can diff against
# last week's, which is what a baseline is for.
#
# USAGE
#   scripts/test_baseline.sh                  # all modules
#   scripts/test_baseline.sh kai geo          # only modules matching these
#   TIMEOUT=300 scripts/test_baseline.sh      # per-module timeout (default 180s)
#   DB_BACKEND=sqlite scripts/test_baseline.sh
#
# Writes a summary table to stdout and full per-module logs to
# .test-baseline/<module>.log — that directory is gitignored, and the logs are
# where you look when a module reports FAIL.
#
# EXIT CODE
#   0 if every module passed, 1 otherwise. Safe to put in `make` or a pre-push
#   hook once the baseline is actually green.

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

: "${TIMEOUT:=180}"
: "${DB_BACKEND:=sqlite}"
export DB_BACKEND

LOGDIR=".test-baseline"
mkdir -p "$LOGDIR"

# Discover test modules rather than hardcoding a list — a hardcoded list silently
# stops covering new modules, which is the failure this codebase keeps hitting.
#
# v3.2x — tests live under src/tests/<domain>/test_*.py now (not flat in src/).
# LABELS holds the dotted manage.py label (src.tests.<domain>.test_x); MODULES
# holds the matching short name (test_x) for the display table and per-module
# log filenames. Filtering by pattern matches against the label, so a domain
# name like "kai" or "guards" works as a filter alongside a filename fragment.
mapfile -t LABELS < <(
    find src/tests -name 'test_*.py' \
    | sed -E 's#^src/(.*)\.py$#\1#; s#/#.#g' \
    | sed 's/^/src./' | sort
)
mapfile -t MODULES < <(
    printf '%s\n' "${LABELS[@]}" | sed -E 's/.*\.//'
)

if [ $# -gt 0 ]; then
    FILTERED_LABELS=(); FILTERED_MODULES=()
    for i in "${!LABELS[@]}"; do
        for pat in "$@"; do
            if [[ "${LABELS[$i]}" == *"$pat"* ]]; then
                FILTERED_LABELS+=("${LABELS[$i]}")
                FILTERED_MODULES+=("${MODULES[$i]}")
                break
            fi
        done
    done
    LABELS=("${FILTERED_LABELS[@]}")
    MODULES=("${FILTERED_MODULES[@]}")
fi

if [ ${#MODULES[@]} -eq 0 ]; then
    echo "No test modules matched." >&2
    exit 1
fi

echo "Test baseline — $(date '+%Y-%m-%d %H:%M')"
echo "DB_BACKEND=$DB_BACKEND, per-module timeout ${TIMEOUT}s, ${#MODULES[@]} modules"
echo

printf '%-42s %8s %6s %6s %6s %8s\n' MODULE STATUS RAN FAIL ERR SECONDS
printf '%.0s─' {1..82}; echo

total_ran=0; total_fail=0; total_err=0
declare -a red=() timedout=()

for i in "${!MODULES[@]}"; do
    mod="${MODULES[$i]}"
    label="${LABELS[$i]}"
    log="$LOGDIR/$mod.log"
    start=$(date +%s)
    timeout "$TIMEOUT" python3 manage.py test "$label" -v 1 > "$log" 2>&1
    rc=$?
    elapsed=$(( $(date +%s) - start ))

    ran=$(grep -oE '^Ran [0-9]+ test' "$log" | grep -oE '[0-9]+' | head -1)
    ran=${ran:-0}
    fail=$(grep -oE 'failures=[0-9]+' "$log" | grep -oE '[0-9]+' | head -1)
    fail=${fail:-0}
    err=$(grep -oE 'errors=[0-9]+' "$log" | grep -oE '[0-9]+' | head -1)
    err=${err:-0}

    if [ $rc -eq 124 ]; then
        status="TIMEOUT"; timedout+=("$mod")
    elif [ $rc -eq 0 ]; then
        status="ok"
    else
        status="FAIL"; red+=("$mod")
    fi

    total_ran=$(( total_ran + ran ))
    total_fail=$(( total_fail + fail ))
    total_err=$(( total_err + err ))

    printf '%-42s %8s %6s %6s %6s %8s\n' "$mod" "$status" "$ran" "$fail" "$err" "$elapsed"
done

printf '%.0s─' {1..82}; echo
printf '%-42s %8s %6s %6s %6s\n' TOTAL '' "$total_ran" "$total_fail" "$total_err"
echo

if [ ${#red[@]} -gt 0 ]; then
    echo "RED (${#red[@]}):"
    for m in "${red[@]}"; do echo "  $m   → $LOGDIR/$m.log"; done
fi
if [ ${#timedout[@]} -gt 0 ]; then
    echo "TIMED OUT (${#timedout[@]}) — raise TIMEOUT or investigate:"
    for m in "${timedout[@]}"; do echo "  $m"; done
fi
if [ ${#red[@]} -eq 0 ] && [ ${#timedout[@]} -eq 0 ]; then
    echo "All ${#MODULES[@]} modules green."
    exit 0
fi

echo
echo "Record this table in Claude/Reports/ so the next run has something to diff."
exit 1
