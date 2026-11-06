"""
The admin console look for Admin v2 ("Royal console", `admin_console_ui` flag).

An AWS-console layout in the site's own colors: a royal-blue top bar with a
Services menu and the ⌘K search, a service sidebar (with Favorites and Recently
visited), breadcrumbs, resource lists with an Actions menu, and Azure-style
slide-out panels for an item's detail. The classic ("360") look comes back
whole when the flag is off: templates/admin_v2/base.html picks
`admin_v2/base_classic.html` (the old layout, unchanged) or
`admin_v2/console/base.html` (the frame).

Where things live:

* the flag: `admin_console_ui` (category admin, seeded ON; not in
  `DISABLED_BY_DEFAULT`, so a missing row is ON too). Templates read
  ``admin_console_ui`` from the feature_flags context processor (resolved from
  the query it already makes, so no extra query); Python reads
  `console_enabled(request)`, which goes through that same cached processor.
* page grouping and icons: `CONSOLE_GROUPS` / `CONSOLE_ICONS` in
  src/admin_v2_nav.py, next to `NAV` (labels, keywords, active names).
* favorites: per admin, in ``UserPreferences.prefs['admin_console']`` (read
  through the user-prefs cache the context processor already loaded, so
  showing them costs no query; the Preferences form preserves the section).
* recently visited: per session (``request.session['admin_console_recent']``).
* the frame's data: `frame(request)`, memoised on the request, read by the
  ``{% admin_console_frame %}`` tag.
* the home page widgets: `home_widgets()` (service health, alarms, sign-ins
  for the last 7 days, resources), added to the dashboard's own context.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from src.admin_v2_nav import (
    CONSOLE_HOME, console_group_of, console_groups, console_item, console_page, console_page_names,
)

logger = logging.getLogger(__name__)

FLAG = 'admin_console_ui'
PREFS_SECTION = 'admin_console'
RECENT_SESSION_KEY = 'admin_console_recent'
RECENT_MAX = 6
FAVORITES_MAX = 15
ALARM_CACHE_KEY = 'admin_console_alarms'
ALARM_CACHE_SECONDS = 60


# --------------------------------------------------------------------------
# The switch
# --------------------------------------------------------------------------

def console_enabled(request):
    """Is the console look on for this request? Same answer the templates get
    (``admin_console_ui`` from the feature_flags context processor, which is
    cached for 60 s, so asking here and again in the template is one query)."""
    if request is None:
        return False
    from src.context_processors import feature_flags
    return bool(feature_flags(request).get('admin_console_ui'))


def wants_panel(request):
    """A detail view asked for as a slide-out panel fragment (``?panel=1``)."""
    return request.GET.get('panel') == '1'


def panel_response(response):
    """Mark a rendered panel fragment (a template extending
    admin_v2/console/panel.html):

        return panel_response(render(request, 'admin_v2/member_panel.html', data))

    The ``X-Admin-Panel`` header tells panel.js it got a fragment and not, say,
    the login page a redirect landed on; without it the panel navigates instead.
    (The template name stays a literal in the view, so
    test_referenced_templates_exist can check it.)"""
    response['X-Admin-Panel'] = '1'
    response['Cache-Control'] = 'no-store'
    return response


# --------------------------------------------------------------------------
# Favorites (per admin, in UserPreferences.prefs) and recently visited (session)
# --------------------------------------------------------------------------

def favorite_names(user):
    from src.context_processors import get_user_prefs
    if not getattr(user, 'is_authenticated', False):
        return []
    prefs = get_user_prefs(user).prefs or {}
    valid = set(console_page_names())
    return [n for n in prefs.get(PREFS_SECTION, {}).get('favorites', []) if n in valid]


def toggle_favorite(user, name):
    """Pin or unpin page `name` for `user`. Returns True if it is now a favorite.
    Raises ValueError for a name that isn't a console page."""
    from src.models import UserPreferences

    if name not in console_page_names():
        raise ValueError(name)
    preferences, _ = UserPreferences.objects.get_or_create(user=user)
    prefs = dict(preferences.prefs or {})
    section = dict(prefs.get(PREFS_SECTION, {}))
    favs = [n for n in section.get('favorites', []) if n != name]
    pinned = name not in section.get('favorites', [])
    if pinned:
        favs.append(name)
    section['favorites'] = favs[-FAVORITES_MAX:]
    prefs[PREFS_SECTION] = section
    preferences.prefs = prefs
    preferences.save(update_fields=['prefs', 'updated_at'])
    cache.delete(f'user_prefs_{user.pk}')
    return pinned


def record_visit(request, name):
    """Put console page `name` at the front of this session's recently visited."""
    if not name or name == CONSOLE_HOME or not hasattr(request, 'session'):
        return
    recent = list(request.session.get(RECENT_SESSION_KEY, []))
    if recent[:1] == [name]:
        return  # unchanged: don't rewrite the session
    request.session[RECENT_SESSION_KEY] = ([name] + [n for n in recent if n != name])[:RECENT_MAX]


def recent_names(request):
    valid = set(console_page_names())
    return [n for n in getattr(request, 'session', {}).get(RECENT_SESSION_KEY, []) if n in valid]


# --------------------------------------------------------------------------
# Alarms (the bell): open security alerts, cached for a minute
# --------------------------------------------------------------------------

def alarm_counts():
    """{'alarm': critical+high, 'warning': medium+low, 'total'} over alerts still
    open (new or under investigation). One aggregate, cached 60 s."""
    data = cache.get(ALARM_CACHE_KEY)
    if data is None:
        from src.models import LoginAlert
        try:
            data = LoginAlert.objects.filter(status__in=('new', 'investigating')).aggregate(
                alarm=Count('pk', filter=Q(severity__in=('critical', 'high'))),
                warning=Count('pk', filter=~Q(severity__in=('critical', 'high'))),
            )
        except Exception:  # pre-migration database: no bell rather than a 500
            logger.exception('admin console alarm count failed')
            data = {'alarm': 0, 'warning': 0}
        data['total'] = (data['alarm'] or 0) + (data['warning'] or 0)
        cache.set(ALARM_CACHE_KEY, data, ALARM_CACHE_SECONDS)
    return data


# --------------------------------------------------------------------------
# The frame
# --------------------------------------------------------------------------

def initials(user):
    """'MK' for Mason Kimball: the account button's avatar."""
    name = user.get_display_name() if getattr(user, 'is_authenticated', False) else ''
    parts = [p for p in name.split() if p[:1].isalnum()]
    if not parts:
        return '?'
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else '')).upper()


def frame(request):
    """Everything the console frame draws, once per request.

    {'groups', 'favorites', 'recent', 'group', 'page', 'alarms', 'home'}:
    `group`/`page` are the breadcrumb (page is a nav item dict, or None on a
    page outside the console's list)."""
    memo = getattr(request, '_admin_console_frame', None)
    if memo is not None:
        return memo
    current = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
    favs = favorite_names(getattr(request, 'user', None))
    group, page_name = console_page(current) if current else (None, None)
    if page_name:
        record_visit(request, page_name)
    favorites = [i for i in (console_item(n, current, favs) for n in favs) if i]
    recent = [i for i in (console_item(n, current, favs) for n in recent_names(request)) if i]
    for item in recent:
        item['group'] = console_group_of(item['name'])
    data = {
        'groups': console_groups(request, favs),
        'favorites': favorites,
        'recent': recent,
        'group': group,
        'page': console_item(page_name, current, favs) if page_name else None,
        'home': console_item(CONSOLE_HOME, current, favs),
        'alarms': alarm_counts(),
        'initials': initials(getattr(request, 'user', None)),
    }
    request._admin_console_frame = data
    return data


# --------------------------------------------------------------------------
# Console home widgets
# --------------------------------------------------------------------------

def _safe(fn, fallback):
    try:
        return fn()
    except Exception:  # one broken source must not take the home page down
        logger.exception('admin console widget failed')
        return fallback


def signins_last_7_days(today=None):
    """[{'day', 'label', 'success', 'failed', 'h', 'y', 'fh', 'fy'}] oldest first.
    `h`/`y` (and `fh`/`fy` for failures) place the bars in a 0-60 SVG box, so
    the template draws them with attributes, not style= (CSP)."""
    from django.db.models.functions import TruncDate
    from src.models import LoginHistory

    tz = timezone.get_current_timezone()
    today = today or timezone.localdate()
    days = [today - timedelta(days=n) for n in range(6, -1, -1)]
    start = timezone.make_aware(timezone.datetime.combine(days[0], timezone.datetime.min.time()), tz)
    rows = (LoginHistory.objects.filter(timestamp__gte=start, status__in=('success', 'failed'))
            .annotate(day=TruncDate('timestamp', tzinfo=tz)).values('day', 'status').annotate(n=Count('pk')))
    found = {(r['day'], r['status']): r['n'] for r in rows}
    out = [{'day': d, 'label': d.strftime('%a'), 'success': found.get((d, 'success'), 0),
            'failed': found.get((d, 'failed'), 0)} for d in days]
    top = max([1] + [max(o['success'], o['failed']) for o in out])
    for i, o in enumerate(out):
        o['x'] = i * 40 + 7
        o['fx'] = o['x'] + 15
        o['tx'] = i * 40 + 20
        o['h'] = round(o['success'] / top * 56) if o['success'] else 0
        o['y'] = 60 - o['h']
        o['fh'] = round(o['failed'] / top * 56) if o['failed'] else 0
        o['fy'] = 60 - o['fh']
    return out


def _service(name, status, detail, url_name, icon):
    try:
        url = reverse(url_name)
    except Exception:
        url = None
    return {'name': name, 'status': status, 'detail': detail, 'url': url, 'icon': icon}


def service_health(celery_summary, performance):
    """Website, background jobs, push delivery, backups: (status ok|warn|alarm|unknown)."""
    from django.conf import settings

    services = []

    avg = (performance or {}).get('avg_response_time_ms') or 0
    web_status = 'warn' if settings.DEBUG or avg > 1500 else 'ok'
    web_detail = f'{avg} ms average response' + (' · DEBUG is on' if settings.DEBUG else '')
    services.append(_service('Website', web_status, web_detail, 'admin_v2_system', 'globe'))

    def jobs():
        from src.admin_v2_jobs import overdue_tasks
        overdue = len(overdue_tasks())
        if not celery_summary.get('workers_up'):
            return 'alarm', f'No worker answered · {celery_summary.get("task_count", 0)} scheduled'
        if overdue:
            return 'warn', f'{overdue} job(s) overdue'
        return 'ok', f'Workers up · {celery_summary.get("task_count", 0)} scheduled'
    status, detail = _safe(jobs, ('unknown', 'Could not read the schedule'))
    services.append(_service('Background jobs', status, detail, 'admin_v2_celery_health', 'clock'))

    def push():
        from src.models.mobile import MobilePushTicket
        if not getattr(settings, 'EXPO_ACCESS_TOKEN', ''):
            return 'warn', 'Expo access token not set'
        # Receipts are checked every 15 minutes and the ticket rows deleted;
        # tickets older than an hour mean the receipt check isn't running.
        stuck = MobilePushTicket.objects.filter(created_at__lt=timezone.now() - timedelta(hours=1)).count()
        if stuck:
            return 'warn', f'{stuck} ticket(s) waiting over an hour for a receipt'
        return 'ok', 'Receipts up to date'
    status, detail = _safe(push, ('unknown', 'Could not read push tickets'))
    services.append(_service('Push delivery', status, detail, 'admin_v2_mobile', 'send'))

    def backups():
        from src.view.admin_v2_ops import backups_overview
        b = backups_overview()
        if b['failing']:
            return 'alarm', 'Last restore drill failed'
        if b['error'] or not b['newest']:
            return 'unknown', b['error'] or 'No backup files found'
        if b['stale']:
            return 'warn', f'Newest backup is from {b["newest"]["modified"]:%b %-d}'
        return 'ok', f'Newest {timezone.localtime(b["newest"]["modified"]):%b %-d, %-I:%M %p}'
    status, detail = _safe(backups, ('unknown', 'Could not read backups'))
    services.append(_service('Backups', status, detail, 'admin_v2_backups', 'database'))
    return services


def home_widgets(stats, celery_summary, performance, alarms):
    """The console home's own widgets (the rest comes from the dashboard's context).

    `alarms` is {'alarm', 'warning', 'ok'} from the dashboard's LoginAlert
    aggregate (open critical/high, other open, resolved in 7 days). It also
    primes the top bar's bell, so the frame doesn't count the table again."""
    from src.models.mobile import MobileDevice

    now = timezone.now()
    installs = _safe(lambda: MobileDevice.objects.filter(
        revoked_at__isnull=True, refresh_expires_at__gt=now).count(), 0)
    resources = [
        {'label': 'Active members', 'n': stats['users']['active'], 'url': reverse('admin_v2_manage_users') + '?status=Active', 'icon': 'users'},
        {'label': 'Pledges', 'n': stats['users']['pledges'], 'url': reverse('admin_v2_manage_users') + '?type=Pledge', 'icon': 'user-group'},
        {'label': 'App installs', 'n': installs, 'url': reverse('admin_v2_mobile'), 'icon': 'phone'},
        {'label': 'Scheduled jobs', 'n': celery_summary.get('task_count', 0), 'url': reverse('admin_v2_celery_health'), 'icon': 'clock'},
    ]
    cache.set(ALARM_CACHE_KEY, {'alarm': alarms['alarm'], 'warning': alarms['warning'],
                                'total': alarms['alarm'] + alarms['warning']}, ALARM_CACHE_SECONDS)
    signins = _safe(signins_last_7_days, [])
    return {
        'services': service_health(celery_summary, performance),
        'alarms': alarms,
        'signins': signins,
        'signins_total': sum(d['success'] for d in signins),
        'signins_failed': sum(d['failed'] for d in signins),
        'resources': resources,
    }
