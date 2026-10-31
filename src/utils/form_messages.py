"""
Form errors as flash messages.

A file-upload form is resent by core.js over fetch(), and only the queued
messages come back (see `src/middleware/js_resubmit.py`): the re-rendered
page with its field errors is never shown. A view that re-renders an
invalid form should call this so the member is told what to fix.
"""
from django.contrib import messages


def queue_form_errors(request, form):
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else None
        for error in errors:
            messages.error(request, f'{label}: {error}' if label else error)
