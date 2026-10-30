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
        ('Admin activity', 'admin_v2_admin_activity', (), 'who changed what admins did changes history'),
    ]),
    ('People', [
        ('Members', 'admin_v2_manage_users', ('admin_v2_member', 'admin_v2_edit_user_profile',
                                              'admin_v2_user_login_security'),
         'users brothers profiles admin access'),
        ('Bulk edit', 'admin_v2_bulk_members', (), 'bulk alumni year end transition status roles many'),
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
        ('Page visits', 'admin_v2_page_visits', (), 'analytics traffic'),
        ('API tokens', 'admin_api_tokens', ('admin_api_token_logs',), 'rest api keys'),
        ('URL explorer', 'admin_v2_url_explorer', (), 'routes urls endpoints'),
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


def search_pages(q, limit=8):
    """Admin pages whose label, group or keywords contain every word of `q`."""
    words = q.lower().split()
    if not words:
        return []
    hits = []
    for title, items in NAV:
        for label, name, _also, kw in items:
            hay = f'{label} {title} {kw}'.lower()
            if all(w in hay for w in words):
                url = _url(name)
                if url:
                    # Label matches rank above keyword-only matches.
                    hits.append((0 if words[0] in label.lower() else 1, label, title, url))
    hits.sort()
    return [{'label': label, 'group': group, 'url': url} for _r, label, group, url in hits[:limit]]
