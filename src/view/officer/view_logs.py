from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.conf import settings
import os
import re

LOG_FILE_PATH = os.path.join(settings.BASE_DIR, 'logs', 'django_actions.log')

# Pattern to match: 2026-02-06 16:19:46,547 [INFO] django.server: message
LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[(\w+)\] ([^:]+): (.*)$')

@login_required
@officer_or_advisor_required
def view_logs(request):
    logs = []

    try:
        print(f"[VIEW_LOGS] Log file path: {LOG_FILE_PATH}")
        print(f"[VIEW_LOGS] File exists: {os.path.exists(LOG_FILE_PATH)}")

        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'r') as f:
                log_lines = f.readlines()[-200:]  # Show last 200 lines for performance
                print(f"[VIEW_LOGS] Read {len(log_lines)} lines from file")

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

            print(f"[VIEW_LOGS] Parsed {len(logs)} log entries")
        else:
            messages.warning(request, "Log file not found.")
            print("[VIEW_LOGS] Log file not found!")
    except Exception as e:
        messages.error(request, f"Error reading log file: {e}")
        print(f"[VIEW_LOGS] Exception: {e}")
        import traceback
        traceback.print_exc()

    print(f"[VIEW_LOGS] Returning {len(logs)} logs to template")
    return render(request, 'admin/view_logs.html', {'logs': logs})
