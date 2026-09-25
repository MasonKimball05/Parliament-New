"""
Error alerting — a 500 should reach a human (09-25-26).

WHY: the email-address login 500 existed from 06-11-26 to 09-24-26 and was
found only because Mason read the server log. settings.py had no ADMINS, no
mail_admins handler and no error tracker, so a crash on a page nobody
reported was invisible.

WHAT: a logging handler on `django.request` (the logger Django writes every
unhandled view exception to). For each ERROR record it emails
SECURITY_ALERT_EMAIL (or ERROR_ALERT_EMAIL if set) through the existing
`tasks.send_email` Celery task.

DELIBERATELY NOT Django's AdminEmailHandler, because that one includes the
request POST body, cookies, headers and every frame's local variables. Here:

  * ✅ method, URL PATTERN (not the concrete path — see `_safe_path`), status,
    user pk, exception type + message, traceback with file/line/function only.
  * ❌ POST data, cookies, headers, session, frame locals — never.
  * ❌ Kai paths (any path or route containing `kai`, which includes
    `committee/<code>/kai-permissions/`): the exception MESSAGE is dropped too (an
    IntegrityError/ValueError message can quote row values). The standing
    Kai confidentiality boundary applies to alert email like everywhere else.

RATE LIMITED, so a crash loop is one email, not a thousand: one alert per
distinct error (type + innermost app frame) per hour, and at most
MAX_ALERTS_PER_HOUR in total. The count of suppressed repeats is included
in the next alert for that error.

MUST NEVER RAISE: a handler that throws while reporting an error turns one
500 into a worse one. Everything is wrapped; failures go to stderr via
logging.Handler.handleError.
"""
import datetime
import hashlib
from pathlib import Path
import logging
import traceback

MAX_ALERTS_PER_HOUR = 10
WINDOW_SECONDS = 3600
MAX_TRACEBACK_CHARS = 8000
# Suppressed repeats are tallied under a key that outlives the one-hour send
# window, so the next alert for the same error can say how many were missed.
SUPPRESSED_TTL_SECONDS = 7 * 24 * 3600


def _safe_path(request):
    """
    The URL PATTERN that matched (`calendar/feed/<str:token>/`), never the
    concrete path.

    ⚠️ 09-25-26 (auto-run finding) — the first version emailed `request.path`
    and its docstring said tokens only live in the query string. They don't:
    the calendar feed, password-reset confirm, 2FA-recovery confirm,
    email-change confirm and event check-in routes all carry a live token IN
    THE PATH. A 500 on any of them would have put that token in an inbox.
    With no resolver match (a 404 or a middleware error) the path is reported
    with every segment after the first replaced by `…` — enough to find the
    area of the site, not enough to carry a credential.
    """
    match = getattr(request, 'resolver_match', None)
    route = getattr(match, 'route', None) if match is not None else None
    if route:
        return '/' + route.lstrip('^').rstrip('$')
    path = getattr(request, 'path', '') or ''
    parts = [p for p in path.split('/') if p]
    if not parts:
        return '/'
    return '/' + parts[0] + ('/…' if len(parts) > 1 else '') + '/'


def _tally_suppressed(cache, key):
    if cache.add(key, 1, SUPPRESSED_TTL_SECONDS) is False:
        try:
            cache.incr(key)
        except ValueError:  # expired between add() and incr()
            cache.set(key, 1, SUPPRESSED_TTL_SECONDS)


def _frames_only(exc_info):
    """Traceback lines (file, line, function, source line) — no locals."""
    etype, evalue, tb = exc_info
    return ''.join(traceback.format_tb(tb))[-MAX_TRACEBACK_CHARS:]


def _signature(exc_info):
    """Stable key for 'the same error': type + innermost frame inside src/."""
    etype, evalue, tb = exc_info
    frames = traceback.extract_tb(tb)
    app = [f for f in frames if 'src' in Path(f.filename).parts] or frames
    last = app[-1] if app else None
    raw = f'{etype.__name__}|{last.filename if last else ""}|{last.lineno if last else ""}'
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class ErrorAlertHandler(logging.Handler):
    def __init__(self, level=logging.ERROR):
        super().__init__(level=level)

    def emit(self, record):
        try:
            self._emit(record)
        except Exception:  # pragma: no cover - reporting must never raise
            self.handleError(record)

    def _emit(self, record):
        if not record.exc_info or not record.exc_info[0]:
            return
        from django.conf import settings
        from django.core.cache import cache

        exc_info = record.exc_info
        sig = _signature(exc_info)
        count_key = f'error_alert:count:{sig}'
        suppressed_key = f'error_alert:suppressed:{sig}'
        # Send only the first occurrence in each window; tally the rest under
        # a longer-lived key that the next alert for this error reports.
        if cache.add(count_key, 0, WINDOW_SECONDS) is False:
            _tally_suppressed(cache, suppressed_key)
            return
        global_key = 'error_alert:sent_this_hour'
        cache.add(global_key, 0, WINDOW_SECONDS)
        try:
            sent = cache.incr(global_key)
        except ValueError:
            cache.set(global_key, 1, WINDOW_SECONDS)
            sent = 1
        if sent > MAX_ALERTS_PER_HOUR:
            # Over the global cap: this occurrence is not sent either, so it
            # counts as suppressed for the next alert of this error.
            _tally_suppressed(cache, suppressed_key)
            return

        request = getattr(record, 'request', None)
        raw_path = getattr(request, 'path', '') or ''
        path = _safe_path(request) if request is not None else ''
        method = getattr(request, 'method', '') or ''
        user = getattr(request, 'user', None)
        user_pk = getattr(user, 'pk', None) if getattr(user, 'is_authenticated', False) else None
        is_kai = 'kai' in raw_path.lower() or 'kai' in path.lower()
        etype, evalue, _tb = exc_info
        message = '[redacted — Kai path]' if is_kai else str(evalue)[:500]

        suppressed = cache.get(suppressed_key) or 0
        if suppressed:
            cache.delete(suppressed_key)
        repeats = (f'{suppressed} earlier occurrence(s) of this error were not emailed '
                   f'(rate limit).\n' if suppressed else '')
        when = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)

        subject = f'[Parliament 500] {etype.__name__} at {method} {path}'[:200]
        body = (
            f'An unhandled error occurred.\n\n'
            f'When:      {when:%Y-%m-%d %H:%M:%S} UTC\n'
            f'Request:   {method} {path}\n'
            f'Status:    {getattr(record, "status_code", 500)}\n'
            f'User pk:   {user_pk or "anonymous"}\n'
            f'Error:     {etype.__name__}: {message}\n'
            f'Signature: {sig} (repeats of this error are counted, not re-sent, for an hour)\n'
            f'{repeats}\n'
            f'Traceback (frames only — no request data, no local variables):\n'
            f'{_frames_only(exc_info)}\n'
        )
        recipient = getattr(settings, 'ERROR_ALERT_EMAIL', '') or getattr(settings, 'SECURITY_ALERT_EMAIL', '')
        if not recipient:
            return
        from src.tasks.email import send_email
        send_email.delay(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], True)
