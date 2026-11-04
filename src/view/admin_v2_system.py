"""
Admin v2: system tools (v4.4.4).

Three things an admin otherwise needs a server shell for:

1. CLEAR CACHE, BY NAMED SCOPE ONLY. There is deliberately no "clear
   everything". In production the cache is Redis and it is ALSO the session
   store (`SESSION_ENGINE = ...sessions.backends.cache` in settings.py), and it
   holds every rate-limit and lockout counter (`login_attempts_*`,
   `login_lockout_*`, `admin_v2_attempts_*`, `2fa_failures_*`, password-reset
   and passkey re-auth counters), the IP block and honeypot bans, and the
   once-only markers that stop a notification going out twice
   (`signup_ann_sent_*`, `token_expiry_notif_*`, `watch_flag_alerted_*`). A
   full `cache.clear()` would sign out the whole chapter, reset every lockout
   mid-attack and re-send notifications. Each scope below deletes only keys
   the codebase rebuilds from the database on the next read, using the
   models' own `invalidate_cache()` helpers where they exist.

2. RUN A SCHEDULED JOB NOW. Lists the django-celery-beat PeriodicTask rows (as
   the Scheduled jobs page does) and queues one with `send_task`, with the
   row's own args/kwargs/queue. Not offered: disabled rows (someone turned it
   off on purpose; turn it back on first) and anything that erases data
   (`tasks.purge_deleted_accounts`, or a task name with purge / erase / wipe /
   destroy / drop in it): permanent erasure stays on its own timer. The same
   job can be queued at most once a minute, so a double click is one run.

3. RECENT SERVER ERRORS AND SLOW PAGES, from what the site already records:
   ERROR / CRITICAL lines (with their tracebacks) from the tail of the log
   file the `file` handler writes (settings.LOGGING), and the slowest
   requests in PerformanceMiddleware's sample buffer
   (src/middleware/performance.py). There is no error table and no Sentry.
   Everything shown goes through `redact()` (passwords, tokens, cookies,
   Authorization headers, credentials in URLs, long token-like strings and
   token-like URL path segments) and the Kai log redaction System logs uses.

Every action is POST-only, needs `confirm=yes` from the confirm panel (a GET
with `?confirm=` only shows the panel), and writes a `settings_changed`
ActivityLog row like the dashboard's switches do.
"""
import json
import logging
import os
import re

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.admin_v2_jobs import task_status
from src.models import ActivityLog, ParliamentUser
from src.view.admin_v2 import require_admin_v2_auth

logger = logging.getLogger(__name__)

# ── 1. Cache scopes ──────────────────────────────────────────────────────────


def _clear_settings():
    from src.models import SystemLockdown
    from src.models.education import PledgePageRestriction
    from src.models_feature_flags import FeatureFlag, PageToggle, SiteSetting

    FeatureFlag.invalidate_cache()
    PageToggle.invalidate_cache()
    SiteSetting.invalidate_cache()
    SystemLockdown.invalidate_cache()
    keys = ['context_feature_flags']
    keys += [f'pledge_restriction_{n}' for n in PledgePageRestriction.objects.values_list('url_name', flat=True)]
    cache.delete_many(keys)


def _changelog_versions():
    try:
        return [n[:-3] for n in os.listdir(os.path.join(settings.BASE_DIR, 'changelogs')) if n.endswith('.md')]
    except OSError:
        return []


def _clear_content():
    from src.models import LandingPageContent, SongCategory

    LandingPageContent.invalidate_cache()
    SongCategory.invalidate_tabs_cache()
    versions = _changelog_versions()
    keys = ['changelog_versions_v1', 'changelog_index_v1', 'mobile_changelog_sections_v1', 'groupme_groups_list']
    keys += [f'changelog_detail_v1:{v}' for v in versions]
    keys += [f'mobile_changelog_detail_v1:{v}' for v in versions]
    cache.delete_many(keys)


def _clear_members():
    pks = list(ParliamentUser.objects.values_list('pk', flat=True))
    keys = [f'{prefix}{pk}' for pk in pks for prefix in MEMBER_KEY_PREFIXES]
    for i in range(0, len(keys), 500):
        cache.delete_many(keys[i:i + 500])


def _clear_reports():
    from src.view.admin_v2_health import cache_keys

    keys = cache_keys()
    try:
        mtime = int(os.path.getmtime(os.path.join(settings.BASE_DIR, 'src', 'urls.py')))
    except OSError:
        mtime = 0
    keys += [f'admin_v2_url_explorer_data_{mtime}', 'admin_v2_url_explorer_data_0']
    cache.delete_many(keys)


def _clear_performance():
    from src.middleware import performance

    cache.delete_many([performance.CACHE_KEY, performance.COUNT_KEY, performance.SAMPLED_KEY])


#: Per-member display caches (src/context_processors.py, src/dev_mode.py):
#: preferences, the notification and chat badges, the 2FA banner state. All
#: recomputed from the database on the member's next page.
MEMBER_KEY_PREFIXES = ('user_prefs_', 'notif_count_', 'chat_unread_', '2fa_status_')

#: scope -> (label, what it does, clear function)
SCOPES = {
    'settings': ('Feature flags, page switches and settings',
                 'Every feature flag, page on/off switch, site setting, the lockdown state and the pledge '
                 'page restrictions are re-read from the database. Use after editing them outside the '
                 'admin pages.', _clear_settings),
    'content': ('Page content',
                'The landing page, the songbook tabs, the changelog pages (website and app) and the GroupMe '
                'group list.', _clear_content),
    'members': ("Members' badges and preferences",
                "Each member's cached preferences, notification and chat counts and two-factor banner state. "
                'Use if a badge or setting looks stuck for someone.', _clear_members),
    'reports': ('Admin reports',
                'Chapter health and the URL explorer are rebuilt on their next load.', _clear_reports),
    'performance': ('Slow-page samples',
                    'Empties the request timing buffer below and its counters. The slow-pages list starts '
                    'over; nothing else changes.', _clear_performance),
}

#: Shown on the page so nobody goes looking for the missing button.
NOT_OFFERED = (
    ('Clear everything', 'Sessions live in the same cache in production: it would sign out every member.'),
    ('Sign-in rate limits and lockouts', 'Failed-attempt counters and lockouts reset mid-attack. Clear a '
                                         'lockout one at a time on the Lockouts page.'),
    ('IP blocks and honeypot bans', 'Managed on the IP block list and Honeypot pages.'),
    ('Notification once-only markers', 'They stop sign-up announcements, token-expiry and watch alerts '
                                       'from going out twice.'),
)

# ── 2. Scheduled jobs ────────────────────────────────────────────────────────

#: Erases data for good; it keeps its own 30-day timer (src/account_deletion.py).
BLOCKED_TASKS = {'tasks.purge_deleted_accounts'}
BLOCKED_WORDS = ('purge', 'erase', 'wipe', 'destroy', 'drop')
RUN_COOLDOWN_SECONDS = 60


def job_refusal(task):
    """Why `task` can't be run from here, or None."""
    name = (task.task or '').lower()
    if task.task in BLOCKED_TASKS or any(w in name for w in BLOCKED_WORDS):
        return 'Erases data permanently, so it only runs on its own schedule.'
    if not task.enabled:
        return 'Disabled. Turn it back on before running it.'
    return None


def job_rows():
    from django_celery_beat.models import PeriodicTask

    now = timezone.now()
    rows = []
    for task in PeriodicTask.objects.select_related('interval', 'crontab', 'solar', 'clocked').order_by('name'):
        status, next_due = task_status(task, now)
        rows.append({'task': task, 'status': status, 'next_due': next_due, 'refusal': job_refusal(task)})
    return rows


def queue_job(task):
    """Queue one run of the PeriodicTask row now. Returns the Celery task id."""
    from celery import current_app

    args = json.loads(task.args or '[]')
    kwargs = json.loads(task.kwargs or '{}')
    options = {'queue': task.queue} if task.queue else {}
    return current_app.send_task(task.task, args=args, kwargs=kwargs, **options).id

# ── 3. Errors and slow pages ─────────────────────────────────────────────────


#: key=value / key: value pairs whose value is a secret. The key may carry a
#: prefix or suffix (`access_token`, `csrftoken`, `X-Site-Monitor-Token`).
_SECRET_KEYS = (r'password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|sessionid|'
                r'session[_-]?key|csrf|cookie|authorization|refresh|otp|totp|signature|dsn|credential')
_PAIR_RE = re.compile(r'''(?i)(["']?[\w-]*(?:%s)[\w-]*["']?\s*[=:]\s*)(["']?)([^\s"'&,;}]+)''' % _SECRET_KEYS)
#: Short query parameters that carry one-time values (`?code=`, `?sig=`, `?key=`).
_QUERY_RE = re.compile(r'(?i)([?&](?:code|sig|key|k|t|uid)=)[^&\s"\']+')
_HEADER_RE = re.compile(r'(?i)\b(cookie|set-cookie|authorization|proxy-authorization)(\s*[:=]\s*)(.+)$')
_BEARER_RE = re.compile(r'(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}')
_URL_CRED_RE = re.compile(r'([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@', re.I)
_TOKEN_RUN_RE = re.compile(r'(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])')
REDACTED = '[redacted]'


def _looks_like_token(s):
    """A long run of token characters that is not an identifier: four or more
    digits (hex digests, signed tokens, uuids), or 32+ characters in mixed
    case (base64). `admin_v2_member_revoke_device` survives."""
    if len(s) < 20:
        return False
    digits = sum(c.isdigit() for c in s)
    return digits >= 4 or (len(s) >= 32 and any(c.isupper() for c in s) and any(c.islower() for c in s))


def redact(text):
    """`text` with anything that looks like a credential replaced. Never raises."""
    if not text:
        return text or ''
    text = _HEADER_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}{REDACTED}', text)
    text = _URL_CRED_RE.sub(lambda m: f'{m.group(1)}{REDACTED}@', text)
    text = _BEARER_RE.sub(lambda m: f'{m.group(1)} {REDACTED}', text)
    text = _PAIR_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}{REDACTED}', text)
    text = _QUERY_RE.sub(lambda m: f'{m.group(1)}{REDACTED}', text)
    return _TOKEN_RUN_RE.sub(lambda m: REDACTED if _looks_like_token(m.group(0)) else m.group(0), text)


def redact_path(path):
    """A URL path with token-like segments (and any query string) removed."""
    path = (path or '').split('?', 1)[0]
    return '/'.join(REDACTED if _looks_like_token(seg) else seg for seg in path.split('/'))


LOG_TAIL_BYTES = 512 * 1024
ERROR_LIMIT = 40
TRACE_LINES = 40
ERROR_LEVELS = ('ERROR', 'CRITICAL')


def log_file_path():
    """The file the `file` logging handler writes (settings.LOGGING), or None."""
    try:
        return settings.LOGGING['handlers']['file']['filename']
    except (AttributeError, KeyError, TypeError):
        return None


def read_errors(path=None, limit=ERROR_LIMIT):
    """
    (entries newest first, error message or None). Each entry is
    {'timestamp', 'level', 'logger', 'message', 'trace'}, already redacted.
    Reads only the last LOG_TAIL_BYTES of the file.
    """
    from src.kai_audit import _username_pattern, kai_log_view_names, member_usernames, redact_kai_log_message
    from src.view.officer.view_logs import LOG_PATTERN, _tail_lines

    path = path or log_file_path()
    if not path:
        return [], 'No log file is configured.'
    if not os.path.exists(path):
        return [], 'The log file does not exist yet.'
    try:
        lines = _tail_lines(path, max_lines=10 ** 6, tail_bytes=LOG_TAIL_BYTES)
    except OSError as exc:
        return [], f'The web server cannot read the log file ({exc.__class__.__name__}).'
    kai_views = kai_log_view_names()
    usernames = _username_pattern(member_usernames())

    def clean(s):
        return redact(redact_kai_log_message(s, kai_views, usernames))

    entries, current = [], None
    for line in lines:
        m = LOG_PATTERN.match(line)
        if m:
            timestamp, level, name, message = m.groups()
            current = None
            if level in ERROR_LEVELS:
                current = {'timestamp': timestamp, 'level': level, 'logger': name,
                           'message': clean(message), 'trace': []}
                entries.append(current)
        elif current is not None and line.strip() and len(current['trace']) < TRACE_LINES:
            current['trace'].append(clean(line.rstrip()))
    entries.reverse()
    return entries[:limit], None


def slow_pages(limit=20):
    from src.middleware.performance import ALWAYS_STORE_ABOVE_MS, get_performance_summary, get_slow_requests

    rows = [{'when': ts, 'ms': round(ms), 'path': redact_path(path), 'queries': q, 'db_ms': round(db)}
            for ts, ms, path, q, db in get_slow_requests(threshold_ms=ALWAYS_STORE_ABOVE_MS, limit=limit)]
    return rows, get_performance_summary(), ALWAYS_STORE_ABOVE_MS

# ── Views ────────────────────────────────────────────────────────────────────


def _log(request, description, object_type, object_id, object_repr, metadata=None):
    ActivityLog.log_activity(
        action_type='settings_changed', user=request.user, description=description,
        object_type=object_type, object_id=str(object_id), object_repr=object_repr,
        metadata=metadata, request=request)


@require_admin_v2_auth
def admin_v2_system(request):
    rows = job_rows()
    confirm = None
    kind = request.GET.get('confirm')
    if kind == 'cache' and request.GET.get('scope') in SCOPES:
        key = request.GET['scope']
        confirm = {'kind': 'cache', 'scope': key, 'label': SCOPES[key][0], 'help': SCOPES[key][1]}
    elif kind == 'job' and (request.GET.get('task') or '').isdigit():
        row = next((r for r in rows if r['task'].pk == int(request.GET['task'])), None)
        if row and not row['refusal']:
            confirm = {'kind': 'job', 'task': row['task']}
    errors, errors_problem = read_errors()
    slow, perf, slow_ms = slow_pages()
    return render(request, 'admin_v2/system_tools.html', {
        'scopes': [(k, label, help_) for k, (label, help_, _fn) in SCOPES.items()],
        'not_offered': NOT_OFFERED,
        'job_rows': rows,
        'confirm': confirm,
        'errors': errors,
        'errors_problem': errors_problem,
        'log_name': os.path.basename(log_file_path() or ''),
        'slow': slow,
        'perf': perf,
        'slow_ms': slow_ms,
    })


def _back(anchor='', **query):
    url = reverse('admin_v2_system')
    if query:
        url += '?' + '&'.join(f'{k}={v}' for k, v in query.items())
    return redirect(url + (f'#{anchor}' if anchor else ''))


@require_admin_v2_auth
@require_POST
def admin_v2_system_clear_cache(request):
    scope = request.POST.get('scope', '')
    if scope not in SCOPES:
        messages.error(request, 'Unknown cache scope.')
        return _back('cache')
    if request.POST.get('confirm') != 'yes':
        messages.warning(request, 'Confirm to clear the cache.')
        return _back('cache', confirm='cache', scope=scope)
    label, _help, clear = SCOPES[scope]
    try:
        clear()
    except Exception:
        logger.exception('admin-v2 cache clear failed: %s', scope)
        messages.error(request, f'Could not clear "{label}". The server log has the reason.')
        return _back('cache')
    _log(request, f'{request.user.get_display_name()} cleared the cache: {label}',
         'Cache', scope, label, {'scope': scope})
    messages.success(request, f'Cleared "{label}". It is rebuilt from the database on the next read.')
    return _back('cache')


@require_admin_v2_auth
@require_POST
def admin_v2_system_run_job(request):
    from django_celery_beat.models import PeriodicTask

    raw = request.POST.get('task', '')
    task = PeriodicTask.objects.filter(pk=int(raw)).first() if raw.isdigit() else None
    if task is None:
        messages.error(request, 'That scheduled job does not exist.')
        return _back('jobs')
    refusal = job_refusal(task)
    if refusal:
        messages.error(request, f'"{task.name}" can\'t be run from here: {refusal}')
        return _back('jobs')
    if request.POST.get('confirm') != 'yes':
        messages.warning(request, 'Confirm to run the job.')
        return _back('jobs', confirm='job', task=task.pk)
    if not cache.add(f'admin_v2_run_job:{task.pk}', 1, RUN_COOLDOWN_SECONDS):
        messages.warning(request, f'"{task.name}" was queued less than a minute ago. Give it a moment.')
        return _back('jobs')
    try:
        task_id = queue_job(task)
    except Exception:
        cache.delete(f'admin_v2_run_job:{task.pk}')
        logger.exception('admin-v2 run job failed: %s', task.task)
        messages.error(request, f'Could not queue "{task.name}" (is the broker up?). The server log has the reason.')
        return _back('jobs')
    _log(request, f'{request.user.get_display_name()} ran a scheduled job now: {task.name}',
         'PeriodicTask', task.pk, task.name, {'task': task.task, 'celery_id': str(task_id)})
    messages.success(request, f'Queued "{task.name}". A worker picks it up within seconds if one is running '
                              '(see Scheduled jobs).')
    return _back('jobs')
