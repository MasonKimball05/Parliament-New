"""
Admin v2 dashboard: "Needs attention" (v3.58.1).

One short list of things an admin should act on, each a count with a link to
the page that handles it. Only non-zero items are shown, so an empty strip
means nothing is waiting. Every count is a single indexed query; a failing
source is skipped rather than breaking the dashboard.
"""
import logging
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def _items():
    from src.models import (
        AnnouncementEmailLog, APIToken, BugReport, ContactSubmission, CSPViolation,
        LoginAlert, LoginLockout, QuarantinedAccount, SystemLockdown,
    )
    from django.db.models import Count, Q

    now = timezone.now()
    day_ago = now - timedelta(days=1)
    # One query for both alert items (the dashboard already counts this table).
    alerts = {}

    def alert_counts():
        if not alerts:
            alerts.update(LoginAlert.objects.filter(status='new').aggregate(
                critical=Count('pk', filter=Q(severity='critical')),
                other=Count('pk', filter=~Q(severity='critical'))))
        return alerts
    # (severity, label, count-or-callable, url name)
    return [
        ('critical', 'Lockdown is ON', lambda: int(SystemLockdown.get_instance().is_active), 'admin_v2_lockdown'),
        ('critical', 'critical security alert(s)',
         lambda: alert_counts()['critical'], 'admin_v2_security_alerts'),
        ('warning', 'new security alert(s)',
         lambda: alert_counts()['other'], 'admin_v2_security_alerts'),
        ('warning', 'locked-out sign-in(s)',
         lambda: LoginLockout.objects.filter(is_cleared=False, expires_at__gt=now).count(), 'admin_v2_lockouts'),
        ('warning', 'quarantined account(s)',
         lambda: QuarantinedAccount.objects.filter(released_at__isnull=True).count(), 'admin_v2_quarantine'),
        ('warning', 'announcement email run(s) failed in 24h',
         lambda: AnnouncementEmailLog.objects.filter(status='failed', created_at__gte=day_ago).count(),
         'admin_v2_email_logs'),
        ('info', 'API token request(s) waiting',
         lambda: APIToken.objects.filter(status=APIToken.STATUS_PENDING).count(), 'admin_api_tokens'),
        ('info', 'new bug report(s)', lambda: BugReport.objects.filter(status='new').count(), 'bug_admin'),
        ('info', 'unread contact message(s)',
         lambda: ContactSubmission.objects.filter(is_read=False).count(), 'contact_submissions'),
        ('info', 'CSP violation(s) to review',
         lambda: CSPViolation.objects.filter(dismissed=False).count(), 'admin_v2_csp_violations'),
    ]


def attention_items():
    """[{'severity', 'n', 'label', 'url'}] for the non-zero items, worst first."""
    out = []
    for severity, label, count_fn, url_name in _items():
        try:
            count = count_fn()
            url = reverse(url_name)
        except Exception:  # one broken source must not take the dashboard down
            logger.exception('admin attention item failed: %s', label)
            continue
        if count:
            out.append({'severity': severity, 'n': count, 'label': label, 'url': url})
    order = {'critical': 0, 'warning': 1, 'info': 2}
    out.sort(key=lambda i: order[i['severity']])
    return out
