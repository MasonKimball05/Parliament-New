#!/usr/bin/env bash
# ============================================================================
# check_uptime.sh — external 502/downtime watchdog for Parliament (v3.15.2)
#
# Emails you if the site has been failing for >= ~15 minutes, and again once
# it recovers. Deliberately a plain shell script on SYSTEM cron so it keeps
# working when the app / Daphne is exactly the thing that's down (the 07-19
# 502 incident). No Django, no Celery, no venv required.
#
# How "15 minutes" works: cron runs this every 5 min; after FAIL_THRESHOLD (3)
# consecutive failures it alerts once, then stays quiet until recovery. Tune
# FAIL_THRESHOLD or the cron interval to change the window.
#
# Install (see scripts/crontab.txt):
#   */5 * * * * /var/www/Parliament-New/scripts/check_uptime.sh
# Set the recipient via env (or rely on the crontab MAILTO):
#   UPTIME_ALERT_EMAIL=mason.kimball05@gmail.com
# ============================================================================
set -uo pipefail

# --- config (override via environment) --------------------------------------
# Hit the ORIGIN directly through nginx so we catch the exact 502 (nginx up,
# Daphne down) without Cloudflare caching masking it. Cache-buster on the path.
URL="${PARLIAMENT_HEALTH_URL:-http://localhost/?_uptime=$(date +%s)}"
HOST_HEADER="${PARLIAMENT_HOST:-am-parliament.org}"
FAIL_THRESHOLD="${UPTIME_FAIL_THRESHOLD:-3}"   # consecutive fails before alert
CURL_TIMEOUT="${UPTIME_CURL_TIMEOUT:-10}"      # seconds
STATE_FILE="${UPTIME_STATE_FILE:-/var/www/Parliament-New/logs/uptime_state}"
ALERT_EMAIL="${UPTIME_ALERT_EMAIL:-}"          # optional direct send via `mail`

# --- probe ------------------------------------------------------------------
code="$(curl -s -o /dev/null -m "$CURL_TIMEOUT" \
        -H "Host: ${HOST_HEADER}" -H 'Cache-Control: no-cache' \
        -w '%{http_code}' "$URL" 2>/dev/null || echo 000)"

# Healthy = 2xx/3xx (302 to login is fine). Everything else (5xx, 000 conn
# refused/timeout) is a failure.
if [[ "$code" =~ ^[23][0-9][0-9]$ ]]; then healthy=1; else healthy=0; fi

# --- state (two ints on one line: "<consecutive_fails> <alerted 0|1>") ------
fails=0; alerted=0
if [[ -r "$STATE_FILE" ]]; then read -r fails alerted < "$STATE_FILE" || true; fi
fails="${fails:-0}"; alerted="${alerted:-0}"

emit() {  # print (→ cron MAILTO) and, if configured, send directly via `mail`
    local subject="$1" body="$2"
    echo "$subject"
    echo "$body"
    if [[ -n "$ALERT_EMAIL" ]] && command -v mail >/dev/null 2>&1; then
        printf '%s\n' "$body" | mail -s "$subject" "$ALERT_EMAIL" || true
    fi
}

if [[ "$healthy" -eq 1 ]]; then
    if [[ "$alerted" -eq 1 ]]; then
        emit "[Parliament] ✅ Site RECOVERED" \
             "am-parliament.org is responding again (HTTP $code) at $(date -u '+%Y-%m-%d %H:%M UTC')."
    fi
    echo "0 0" > "$STATE_FILE"
else
    fails=$((fails + 1))
    if [[ "$fails" -ge "$FAIL_THRESHOLD" && "$alerted" -eq 0 ]]; then
        emit "[Parliament] ⚠ Site DOWN (HTTP $code)" \
             "am-parliament.org has failed ${fails} consecutive checks (HTTP $code) — down for ~$((fails * 5)) min as of $(date -u '+%Y-%m-%d %H:%M UTC'). Check: systemctl status parliament-gunicorn"
        alerted=1
    fi
    echo "$fails $alerted" > "$STATE_FILE"
fi
