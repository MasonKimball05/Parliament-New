from src.decorators import officer_or_advisor_required
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

@login_required
@officer_or_advisor_required
def view_logs(request):
    logs = []

    try:
        logger.debug("view_logs: reading %s (exists=%s)", LOG_FILE_PATH, os.path.exists(LOG_FILE_PATH))

        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'r') as f:
                log_lines = f.readlines()[-200:]  # Show last 200 lines for performance
                logger.debug("view_logs: read %d lines from file", len(log_lines))

                for line in reversed(log_lines):
                    line = line.strip()
                    if not line:
                        continue

                    match = LOG_PATTERN.match(line)
                    if match:
                        timestamp, level, logger_name, message = match.groups()
                        logs.append({
                            'timestamp': timestamp,
                            'logger': logger_name,
                            'level': level,
                            'message': f"[{level}] {message}",
                        })
                    else:
                        # Fallback for lines that don't match the pattern
                        logs.append({
                            'timestamp': '',
                            'logger': '',
                            'level': '',
                            'message': line
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
