from ..models import *
from ..decorators import *
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
import os
LOG_FILE_PATH = os.path.join(settings.BASE_DIR, 'logs')
if not os.path.exists(LOG_FILE_PATH):
    os.makedirs(LOG_FILE_PATH)

@login_required
@user_passes_test(lambda u: hasattr(u, 'is_admin') and u.is_admin)
@log_function_call
def view_logs(request):
    logs = []

    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'r') as f:
                log_lines = f.readlines()[-200:]  # Show last 200 lines for performance
                for line in reversed(log_lines):
                    parts = line.strip().split(" - ")
                    if len(parts) >= 3:
                        timestamp, logger_name, message = parts[0], parts[1], " - ".join(parts[2:])
                        logs.append({
                            'timestamp': timestamp,
                            'logger': logger_name,
                            'message': message,
                        })
                    else:
                        logs.append({
                            'timestamp': '',
                            'logger': '',
                            'message': line.strip()
                        })
        else:
            messages.warning(request, "Log file not found.")
    except Exception as e:
        messages.error(request, f"Error reading log file: {e}")

    return render(request, 'admin/view_logs.html', {'logs': logs})