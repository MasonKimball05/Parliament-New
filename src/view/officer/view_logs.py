from src.decorators import officer_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.conf import settings
import logging
import os
import re

logger = logging.getLogger(__name__)

LOG_FILE_PATH = os.path.join(settings.BASE_DIR, 'logs', 'django_actions.log')

# Pattern to match: 2026-02-06 16:19:46,547 [INFO] django.server: message
LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[(\w+)\] ([^:]+): (.*)$')

# How many lines to show, and how much of the file tail to read to find them.
# 256 KB comfortably holds 200 log lines (~1 KB/line worst case) without ever
# loading the full 10 MB rotation cap into memory per page view. (v3.15.6)
LOG_DISPLAY_LINES = 200
LOG_TAIL_BYTES = 256 * 1024


def _tail_lines(path, max_lines=LOG_DISPLAY_LINES, tail_bytes=LOG_TAIL_BYTES):
    """Read only the last `tail_bytes` of the file and return its final
    `max_lines` complete lines — instead of readlines() on the whole file."""
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        chunk = f.read()
    if size > tail_bytes:
        # Drop the (likely partial) first line of the chunk.
        chunk = chunk.split(b'\n', 1)[-1]
    return chunk.decode('utf-8', errors='replace').splitlines()[-max_lines:]


# ⚠️ v3.25.2 — NARROWED FROM `officer_or_advisor_required` TO `officer_required`.
#
# This page had a WIDER audience than every page whose actions it records.
# `officer_or_advisor_required` admits advisors; `review_excuses`, which is what
# `serve_excuse_document` serves, is `@officer_required` and deliberately does
# not. So an advisor could not open a member's excuse document but could read
# `User <member> called serve_excuse_document {'excuse_id': 7}` in the log tail
# — learning that a named member has a medical excuse on file, which is exactly
# the fact the excuse system withholds from them.
#
# **A raw application log inherits the audience of its narrowest line, not its
# widest reader.** Rather than add a redaction rule per subject area, the page
# now matches the gate of the pages it reports on. Advisors are read-only
# outsiders and a Django log tail is an operations tool; if you want them back
# in, this is a one-word change.
@login_required
@officer_required
def view_logs(request):
    """
    ⚠️ v3.25.2 — THIS PAGE RENDERS A FILE, AND THAT FILE NAMES KAI REPORTERS.

    `@log_function_call` writes `User <username> called <view>` for every view
    it decorates, and thirteen of those views are in `src/view/kai_reports.py`.
    On `submit_kai_report` the caller **is** the reporter, so the last 200 lines
    of `django_actions.log` were handing every officer, chair and advisor the
    one fact `can_view_submitter_identity` exists to withhold. Reproduced
    end-to-end 08-24-26.

    And that was only the shape I happened to be looking at. Enumerating the
    *writers* into this file found two more, both worse:
    `kai_user_dashboard.request_closure` and `request_drop_case` are
    `@login_required` **party-facing** views, and each logged
    `<username> requested … Kai report '<title>' (ID: N)` — a party's identity
    and the case content on one line. Those three sites no longer write it.
    This redaction is for the lines already on disk.

    `redact_kai_log_message` removes the actor of a `User X called <kai view>`
    line, any token equal to a member's username, and any single-quoted run
    (which on a Kai line is case content). Both the view-name set and the
    username pattern are computed **once per request**, not per line — the
    first walks a module, the second is a query.
    """
    from src.kai_audit import (_username_pattern, kai_log_view_names,
                               member_usernames, redact_kai_log_message)

    # Both computed ONCE per request, not per line: the view-name set walks
    # a module and the username pattern is a query.
    kai_views = kai_log_view_names()
    usernames = _username_pattern(member_usernames())
    logs = []

    try:
        logger.debug("view_logs: reading %s (exists=%s)", LOG_FILE_PATH, os.path.exists(LOG_FILE_PATH))

        if os.path.exists(LOG_FILE_PATH):
            log_lines = _tail_lines(LOG_FILE_PATH)
            logger.debug("view_logs: read %d lines from file", len(log_lines))

            for line in reversed(log_lines):
                line = line.strip()
                if not line:
                    continue

                match = LOG_PATTERN.match(line)
                if match:
                    timestamp, level, logger_name, message = match.groups()
                    message = redact_kai_log_message(message, kai_views, usernames)
                    logs.append({
                        'timestamp': timestamp,
                        'logger': logger_name,
                        'level': level,
                        'message': f"[{level}] {message}",
                    })
                else:
                    # Fallback for lines the timestamp pattern did not parse —
                    # a wrapped traceback, say. Redacted too, and with the whole
                    # raw line rather than a message: the redactor searches
                    # rather than anchors precisely so it works on both, and a
                    # line matching neither trigger comes back unchanged.
                    logs.append({
                        'timestamp': '',
                        'logger': '',
                        'level': '',
                        'message': redact_kai_log_message(line, kai_views, usernames)
                    })

            logger.debug("view_logs: parsed %d log entries", len(logs))
        else:
            messages.warning(request, "Log file not found.")
            logger.warning("view_logs: log file not found at %s", LOG_FILE_PATH)
    except Exception as e:
        messages.error(request, f"Error reading log file: {e}")
        logger.exception("view_logs: error reading log file")

    logger.debug("view_logs: returning %d logs to template", len(logs))
    return render(request, 'admin/view_logs.html', {'logs': logs})
