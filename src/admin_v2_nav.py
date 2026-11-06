"""
Admin v2 navigation: the one list the sidebar and the search both read (v4.1.0).

Before, admin-v2 was a single row of 13 tabs, and several admin tools lived
elsewhere on the site (system logs, bug triage, the static-asset monitor) with
no link from admin-v2 at all. Each item here is

    (label, url_name, extra url_names that also mark it active, search keywords)

Grouped so the sidebar can show headings. `nav_groups(request)` resolves the
URLs and the active item; `search_pages(q)` is the page half of the ⌘K search.
"""
from django.urls import NoReverseMatch, reverse

NAV = [
    ('Overview', [
        ('Dashboard', 'admin_v2_dashboard', (), 'home overview stats attention'),
        ('Trends', 'admin_v2_trends', (), 'charts graphs weekly sign ins app adoption failed logins'),
        ('Chapter health', 'admin_v2_health', (),
         'engagement inactive never signed in attendance lowest app installed pledges behind service hours'),
        ('Admin activity', 'admin_v2_admin_activity', (), 'who changed what admins did changes history'),
    ]),
    ('People', [
        ('Members', 'admin_v2_manage_users', ('admin_v2_member', 'admin_v2_edit_user_profile',
                                              'admin_v2_user_login_security'),
         'users brothers profiles admin access'),
        ('Bulk edit', 'admin_v2_bulk_members', (), 'bulk alumni year end transition status roles many'),
        ('Permissions', 'admin_v2_permissions', (), 'who has access roles admins chairs allowlist api tokens kai elevated audit'),
        ('Account deletions', 'admin_v2_account_deletions', (), 'delete deleted account restore rebuild purge former member erase'),
        ('Two-factor', 'admin_v2_two_factor', ('admin_v2_two_factor_update_policy',),
         '2fa totp otp policy'),
        ('Login history', 'admin_v2_login_history', (), 'sign in logins audit'),
        ('Lockouts', 'admin_v2_lockouts', (), 'locked out failed login axes'),
        ('Committees', 'admin_v2_manage_committees', (), 'committee chairs'),
    ]),
    ('Content', [
        ('Announcements', 'admin_v2_manage_announcements', (), 'posts news'),
        ('Events', 'admin_v2_manage_events', (), 'calendar meetings'),
        ('Legislation', 'admin_v2_manage_legislation', (), 'votes bills motions'),
    ]),
    ('Comms', [
        ('Notifications', 'admin_v2_notifications',
         ('admin_v2_notification_schedules', 'admin_v2_notification_logs',
          'admin_v2_notification_log_detail'), 'schedules reminders notification logs'),
        ('Email logs', 'admin_v2_email_logs', ('admin_v2_email_log_detail', 'admin_v2_test_email_targeting'),
         'emails sent bounced announcement emails'),
        ('Event reminders', 'admin_v2_event_reminder_logs', ('admin_v2_event_reminder_log_detail',),
         'reminder emails'),
        ('Mobile app', 'admin_v2_mobile', ('admin_v2_mobile_push',), 'app devices phones expo push ios android'),
        ('Push composer', 'admin_v2_push', ('admin_v2_push_send',), 'send push notification broadcast message group everyone officers committee'),
        ('Web push', 'admin_v2_push_subscriptions', (), 'browser push subscriptions'),
    ]),
    ('Security', [
        ('Security overview', 'admin_v2_security', (), 'security dashboard'),
        ('Alerts', 'admin_v2_security_alerts', (), 'security alerts warnings'),
        ('Audit log', 'admin_v2_audit_log', (), 'audit trail everything'),
        ('Quarantine', 'admin_v2_quarantine', (), 'quarantined users'),
        ('Lockdown', 'admin_v2_lockdown', (), 'lockdown mode emergency'),
        ('IP allow list', 'admin_v2_ip_whitelist', (), 'whitelist ip'),
        ('IP block list', 'admin_v2_ip_blacklist', (), 'blacklist ban ip'),
        ('Honeypot', 'admin_v2_honeypot_logs', (), 'honeypot bots scanners'),
        ('CSP violations', 'admin_v2_csp_violations', (), 'content security policy'),
        ('Security emails', 'admin_v2_security_notifications', (), 'security notification log'),
    ]),
    ('System', [
        ('Exports', 'admin_v2_exports', (), 'csv download spreadsheet roster attendance service hours'),
        ('Backups', 'admin_v2_backups', (), 'database backup restore verify'),
        ('System logs', 'view_logs', (), 'logs errors server log files'),
        ('Bug reports', 'bug_admin', ('bug_admin_update',), 'bugs bug tracker triage'),
        ('Maintenance', 'admin_v2_maintenance', (), 'maintenance mode feature flags pages settings'),
        ('Scheduled jobs', 'admin_v2_celery_health', (), 'celery workers queue tasks beat cron timers overdue'),
        ('System tools', 'admin_v2_system', (), 'clear cache run job now errors slow pages 500 exceptions'),
        ('Page visits', 'admin_v2_page_visits', (), 'analytics traffic'),
        ('API tokens', 'admin_api_tokens', ('admin_api_token_logs',), 'rest api keys'),
        ('URL explorer', 'admin_v2_url_explorer', (), 'routes urls endpoints'),
        ('Site map', 'admin_v2_sitemap', (), 'sitemap tree pages flow chart who can open page access debug'),
    ]),
]


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def nav_groups(request):
    """[{'title', 'items': [{'label', 'url', 'active'}]}], skipping unroutable items."""
    current = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
    groups = []
    for title, items in NAV:
        out = []
        for label, name, also, _kw in items:
            url = _url(name)
            if url is None:
                continue
            out.append({'label': label, 'url': url, 'active': current == name or current in also})
        if out:
            groups.append({'title': title, 'items': out})
    return groups


def search_pages(q, limit=8, console=False):
    """Admin pages whose label, group or keywords contain every word of `q`.

    ``console``: name each page's group (and label) the way the console look
    does, so the ⌘K results match its sidebar. Both group names are searched."""
    words = q.lower().split()
    if not words:
        return []
    hits = []
    for title, items in NAV:
        for label, name, _also, kw in items:
            console_title = console_group_of(name) or 'Console'
            console_label = CONSOLE_LABELS.get(name, label)
            hay = f'{label} {console_label} {title} {console_title} {kw}'.lower()
            if all(w in hay for w in words):
                url = _url(name)
                if url:
                    shown, group = (console_label, console_title) if console else (label, title)
                    # Label matches rank above keyword-only matches.
                    hits.append((0 if words[0] in shown.lower() else 1, shown, group, url))
    hits.sort()
    return [{'label': label, 'group': group, 'url': url} for _r, label, group, url in hits[:limit]]


# ---------------------------------------------------------------------------
# The console look (`admin_console_ui` flag, src/admin_console.py)
# ---------------------------------------------------------------------------
#
# The console groups the SAME pages differently (AWS-style services: Identity,
# Security, Messaging ...). It names pages by url_name only, so `NAV` above stays
# the one source of labels, extra active names and search keywords, and the
# classic sidebar is untouched. `test_console.ConsoleNavTests` checks that every
# NAV page appears here exactly once (plus the home page) and nothing else does:
# a page added to NAV and forgotten here fails the build instead of vanishing
# from the console.

CONSOLE_HOME = 'admin_v2_dashboard'

#: (group title, group icon, [url_name, ...]) in sidebar order.
CONSOLE_GROUPS = [
    ('Identity', 'users', [
        'admin_v2_manage_users', 'admin_v2_bulk_members', 'admin_v2_permissions',
        'admin_v2_two_factor', 'admin_v2_account_deletions', 'admin_v2_manage_committees',
    ]),
    ('Security', 'shield', [
        'admin_v2_security', 'admin_v2_security_alerts', 'admin_v2_login_history',
        'admin_v2_lockouts', 'admin_v2_audit_log', 'admin_v2_quarantine', 'admin_v2_lockdown',
        'admin_v2_ip_whitelist', 'admin_v2_ip_blacklist', 'admin_v2_honeypot_logs',
        'admin_v2_csp_violations', 'admin_v2_security_notifications',
    ]),
    ('Content', 'megaphone', [
        'admin_v2_manage_announcements', 'admin_v2_manage_events', 'admin_v2_manage_legislation',
    ]),
    ('Messaging', 'mail', [
        'admin_v2_notifications', 'admin_v2_email_logs', 'admin_v2_event_reminder_logs',
        'admin_v2_push', 'admin_v2_mobile', 'admin_v2_push_subscriptions',
    ]),
    ('Operations', 'server', [
        'admin_v2_celery_health', 'admin_v2_backups', 'admin_v2_maintenance',
        'admin_v2_system', 'view_logs', 'bug_admin',
    ]),
    ('Reports', 'chart', [
        'admin_v2_trends', 'admin_v2_health', 'admin_v2_admin_activity',
        'admin_v2_page_visits', 'admin_v2_exports',
    ]),
    ('Developer', 'code', [
        'admin_api_tokens', 'admin_v2_url_explorer', 'admin_v2_sitemap',
    ]),
]

#: Console-only label changes (the classic sidebar keeps NAV's label).
CONSOLE_LABELS = {
    'admin_v2_dashboard': 'Console home',
    'admin_v2_mobile': 'App installs',
}

#: One icon per page (names from src/admin_console_icons.py).
CONSOLE_ICONS = {
    'admin_v2_dashboard': 'home',
    'admin_v2_manage_users': 'users', 'admin_v2_bulk_members': 'collection',
    'admin_v2_permissions': 'key', 'admin_v2_two_factor': 'shield',
    'admin_v2_account_deletions': 'user-minus', 'admin_v2_manage_committees': 'user-group',
    'admin_v2_security': 'shield', 'admin_v2_security_alerts': 'alert',
    'admin_v2_login_history': 'login', 'admin_v2_lockouts': 'lock',
    'admin_v2_audit_log': 'doc-search', 'admin_v2_quarantine': 'ban',
    'admin_v2_lockdown': 'exclamation', 'admin_v2_ip_whitelist': 'check-circle',
    'admin_v2_ip_blacklist': 'x-circle', 'admin_v2_honeypot_logs': 'beaker',
    'admin_v2_csp_violations': 'code', 'admin_v2_security_notifications': 'mail',
    'admin_v2_manage_announcements': 'megaphone', 'admin_v2_manage_events': 'calendar',
    'admin_v2_manage_legislation': 'scale',
    'admin_v2_notifications': 'bell', 'admin_v2_email_logs': 'mail',
    'admin_v2_event_reminder_logs': 'clock', 'admin_v2_push': 'send',
    'admin_v2_mobile': 'phone', 'admin_v2_push_subscriptions': 'globe',
    'admin_v2_celery_health': 'clock', 'admin_v2_backups': 'database',
    'admin_v2_maintenance': 'adjustments', 'admin_v2_system': 'cog',
    'view_logs': 'terminal', 'bug_admin': 'exclamation',
    'admin_v2_trends': 'chart', 'admin_v2_health': 'heart',
    'admin_v2_admin_activity': 'clipboard', 'admin_v2_page_visits': 'eye',
    'admin_v2_exports': 'download',
    'admin_api_tokens': 'key', 'admin_v2_url_explorer': 'link', 'admin_v2_sitemap': 'map',
}


def _nav_index():
    """{url_name: (NAV label, also-active names, keywords)} for every NAV page."""
    return {name: (label, also, kw) for _title, items in NAV for label, name, also, kw in items}


def console_page(url_name):
    """(console group title or None for home, page url_name) for any url_name that
    is a NAV page or marks one active (a member page → Identity, Members)."""
    index = _nav_index()
    for name, (_label, also, _kw) in index.items():
        if url_name == name or url_name in also:
            if name == CONSOLE_HOME:
                return None, name
            for title, _icon, names in CONSOLE_GROUPS:
                if name in names:
                    return title, name
    return None, None


def console_item(name, current=None, favorites=()):
    """One console nav entry, or None when the route doesn't exist (same rule as
    `nav_groups`: an unroutable page is skipped, never a 500)."""
    index = _nav_index()
    if name not in index:
        return None
    url = _url(name)
    if url is None:
        return None
    label, also, _kw = index[name]
    return {
        'name': name,
        'label': CONSOLE_LABELS.get(name, label),
        'url': url,
        'icon': CONSOLE_ICONS.get(name, 'dot'),
        'active': current is not None and (current == name or current in also),
        'favorite': name in favorites,
    }


def console_groups(request, favorites=()):
    """[{'title', 'icon', 'items': [...]}] for the console sidebar and Services menu."""
    current = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
    groups = []
    for title, icon, names in CONSOLE_GROUPS:
        items = [i for i in (console_item(n, current, favorites) for n in names) if i]
        if items:
            groups.append({'title': title, 'icon': icon, 'items': items})
    return groups


def console_group_of(name):
    for title, _icon, names in CONSOLE_GROUPS:
        if name in names:
            return title
    return None


def console_page_names():
    """Every page the console links to (home first)."""
    return [CONSOLE_HOME] + [n for _t, _i, names in CONSOLE_GROUPS for n in names]
