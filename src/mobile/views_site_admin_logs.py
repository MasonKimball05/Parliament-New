"""
Site admin in the app (v4.4.4): Admin v2's log pages, read only.

Same gate as the rest of Site admin (``src/mobile/site_admin.py``: allowlist →
404, per-device unlock → 403 ``admin_locked``). Each endpoint runs the website
page's own query helper, so the rows, filters, order and page sizes match:

* email logs — ``email_logs_data`` (newest 50 sends, plus the announcements
  still waiting to email) and the detail page's ``email_log_recipients_by_status``;
* event reminder logs — ``event_reminder_logs_data`` (newest 100, plus upcoming
  events with a reminder still to fire) and ``event_reminder_log_detail_data``;
* login history — ``login_history_queryset`` (50 a page, ``?suspicious=yes``
  and ``?user=``);
* admin activity — ``admin_activity`` / ``admin_actors`` /
  ``admin_activity_actions`` (50 a page, Kai rows left out as on the website);
* security notifications — ``security_notifications_queryset`` (50 a page,
  ``is_admin`` only, as on the website).

Only the fields the website's pages show are sent. The one the website shortens
is shortened the same way (a failed email recipient's error, to 10 words).
"Send now" for a pending announcement email stays on the website.
"""
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import Truncator
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView


def _iso(dt):
    return dt.isoformat() if dt else None


def _param(request, key, max_len):
    return (request.query_params.get(key) or '').strip()[:max_len]


def _name(u):
    return u.name if u else None


def _paged(qs, page_size, page_param):
    page = Paginator(qs, page_size).get_page(page_param)
    return page, {'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
                  'has_next': page.has_next()}


# ── Email logs ───────────────────────────────────────────────────────────────

def _email_log_summary(log):
    return {
        'id': log.pk,
        'announcement': log.announcement.title,
        'created_at': _iso(log.created_at),
        'initiated_by': _name(log.initiated_by),
        'status': log.status, 'status_label': log.get_status_display(),
        'visible_to': log.visible_to_raw or [],
        'total_active_users': log.total_active_users,
        'users_matching_visibility': log.users_matching_visibility,
        'emails_sent': log.emails_sent,
        'emails_failed': log.emails_failed,
    }


class EmailLogsView(SiteAdminAPIView):
    """GET: the Email logs page (``email_logs_data``): pending sends and the newest 50 logs."""
    web_page = 'admin_v2_email_logs'

    def get(self, request):
        from src.view.admin_v2 import email_logs_data
        logs, pending = email_logs_data()
        return Response({
            'pending': [{
                'id': a.pk, 'title': a.title, 'posted_by': _name(a.posted_by), 'posted_at': _iso(a.posted_at),
                'publish_at': _iso(a.publish_at), 'is_published': a.is_published(),
                'visible_to': a.visible_to or [],
            } for a in pending],
            'logs': [_email_log_summary(log) for log in logs],
        })


class EmailLogDetailView(SiteAdminAPIView):
    """GET: one email send (``email_log_recipients_by_status``), recipients grouped by status."""
    web_page = 'admin_v2_email_log_detail'

    def get(self, request, log_id):
        from src.models import AnnouncementEmailLog
        from src.view.admin_v2 import email_log_recipients_by_status
        log = get_object_or_404(AnnouncementEmailLog.objects.select_related('announcement', 'initiated_by'),
                                pk=log_id)
        groups = []
        for label, recipients in email_log_recipients_by_status(log).items():
            # The website's error column appears only in a "Failed" group, cut to 10 words.
            failed = 'Failed' in label
            groups.append({
                'label': label,
                'count': len(recipients),
                'recipients': [{
                    'name': r.user_name, 'email': r.user_email, 'member_type': r.user_member_type,
                    'member_status': r.user_member_status, 'status': r.status,
                    **({'error': Truncator(r.error_message).words(10)} if failed else {}),
                } for r in recipients],
            })
        return Response({
            **_email_log_summary(log),
            'initiated_by': _name(log.initiated_by) or 'System',
            'users_with_valid_email': log.users_with_valid_email,
            'completed_at': _iso(log.completed_at),
            'expanded_member_types': log.expanded_member_types or [],
            'error_message': log.error_message,
            'console_log': log.console_log,
            'groups': groups,
        })


# ── Event reminder logs ──────────────────────────────────────────────────────

def _reminder_log_summary(log):
    return {
        'id': log.pk,
        'event': log.event.title,
        'event_at': _iso(log.event.date_time),
        'slot': log.reminder_slot,
        'sent_at': _iso(log.sent_at),
        'status': log.status, 'status_label': log.get_status_display(),
        'users_eligible': log.users_eligible,
        'users_subscribed': log.users_subscribed,
        'notifications_dispatched': log.notifications_dispatched,
        'users_opted_out': log.users_opted_out,
        'users_with_email': log.users_with_email,
        'emails_dispatched': log.emails_dispatched,
        'users_email_opted_out': log.users_email_opted_out,
    }


def _slots(event):
    """The pending page's reminder chips: an enabled slot, still to fire or already sent."""
    out = []
    for n in (1, 2):
        if not getattr(event, f'reminder_{n}_enabled'):
            continue
        sent_at = getattr(event, f'reminder_{n}_sent_at')
        out.append({
            'slot': n,
            'hours_before': getattr(event, f'reminder_{n}_hours_before'),
            'email': bool(getattr(event, f'reminder_{n}_email_enabled')),
            'sent_at': _iso(sent_at),
        })
    return out


class EventReminderLogsView(SiteAdminAPIView):
    """GET: the Event reminder logs page (``event_reminder_logs_data``): pending events and the newest 100."""
    web_page = 'admin_v2_event_reminder_logs'

    def get(self, request):
        from src.view.admin_v2 import event_reminder_logs_data
        logs, pending = event_reminder_logs_data(timezone.now())
        return Response({
            'pending': [{
                'id': e.pk, 'title': e.title, 'date_time': _iso(e.date_time), 'location': e.location or '',
                'reminders': _slots(e),
            } for e in pending],
            'logs': [{**_reminder_log_summary(log), 'emails_viewed': log.emails_viewed} for log in logs],
        })


class EventReminderLogDetailView(SiteAdminAPIView):
    """GET: one reminder dispatch (``event_reminder_log_detail_data``), per member."""
    web_page = 'admin_v2_event_reminder_log_detail'

    def get(self, request, log_id):
        from src.models import EventReminderLog
        from src.view.admin_v2 import event_reminder_log_detail_data
        log = get_object_or_404(EventReminderLog.objects.select_related('event'), pk=log_id)
        d = event_reminder_log_detail_data(log)

        def row(r, push_status=False):
            out = {'name': r.user_name, 'member_type': r.user_member_type,
                   'email_status': r.email_status,
                   'email_status_label': r.get_email_status_display() if r.email_status else ''}
            if push_status:
                out.update(status=r.status, status_label=r.get_status_display())
            return out

        return Response({
            **_reminder_log_summary(log),
            'location': log.event.location or '',
            'error_message': log.error_message,
            'email_enabled': d['email_enabled'],
            'emails_viewed': [{'name': r.user_name, 'member_type': r.user_member_type,
                               'viewed_at': _iso(r.viewed_at)} for r in d['emails_viewed']],
            'emails_not_viewed': [{'name': r.user_name, 'member_type': r.user_member_type}
                                  for r in d['emails_not_viewed']],
            'dispatched': [row(r) for r in d['dispatched']],
            'skipped': [row(r, push_status=True) for r in d['skipped']],
        })


# ── Login history ────────────────────────────────────────────────────────────

class LoginHistoryView(SiteAdminAPIView):
    """GET ``?suspicious=yes&user=&page=``: the Login history page (``login_history_queryset``)."""
    web_page = 'admin_v2_login_history'

    def get(self, request):
        from src.view.admin_v2 import LOGIN_HISTORY_PAGE_SIZE, login_history_queryset
        suspicious = _param(request, 'suspicious', 10)
        user = _param(request, 'user', 100)
        page, meta = _paged(login_history_queryset(suspicious, user), LOGIN_HISTORY_PAGE_SIZE,
                            request.query_params.get('page'))
        return Response({
            'logins': [{
                'id': lh.pk, 'member': lh.user.get_display_name() if lh.user else None,
                'timestamp': _iso(lh.timestamp), 'ip_address': lh.ip_address,
                'city': lh.city, 'region': lh.region, 'device_type': lh.device_type,
                'is_suspicious': lh.is_suspicious,
            } for lh in page.object_list],
            **meta,
        })


# ── Admin activity ───────────────────────────────────────────────────────────

class AdminActivityView(SiteAdminAPIView):
    """GET ``?actor=&action=&q=&page=``: what admins did (``admin_activity``), Kai rows left out."""
    web_page = 'admin_v2_admin_activity'

    def get(self, request):
        from src.models import ActivityLog
        from src.view.admin_v2_activity import (
            PAGE_SIZE, admin_activity, admin_activity_actions, admin_actors,
        )
        actor = _param(request, 'actor', 40)
        action = _param(request, 'action', 50)
        q = _param(request, 'q', 100)
        page, meta = _paged(admin_activity(request.user, actor, action, q), PAGE_SIZE,
                            request.query_params.get('page'))
        body = {
            'entries': [{
                'id': log.pk, 'description': log.description, 'timestamp': _iso(log.timestamp),
                'admin': log.user.get_display_name() if log.user else None,
                'action': log.action_type, 'action_label': log.get_action_type_display(),
                # The website links a member row to the member's admin page.
                'member': ({'user_id': str(log.object_id), 'name': log.object_repr or 'member'}
                           if log.object_type == 'ParliamentUser' and log.object_id else None),
            } for log in page.object_list],
            **meta,
        }
        if page.number == 1:
            body['actors'] = [{'user_id': str(a.user_id), 'name': a.get_display_name()} for a in
                              admin_actors().order_by('name').only('user_id', 'name', 'preferred_name')]
            labels = dict(ActivityLog.ACTION_TYPES)
            body['actions'] = [{'code': a, 'label': labels.get(a, a.replace('_', ' ').capitalize())}
                               for a in admin_activity_actions()]
        return Response(body)


# ── Security notifications ───────────────────────────────────────────────────

class SecurityNotificationsView(SiteAdminAPIView):
    """GET ``?severity=&event_type=&page=``: the Security notifications log, read only (is_admin, as on the website)."""
    web_page = 'admin_v2_security_notifications'
    needs_is_admin = True

    def get(self, request):
        from src.view.admin_v2 import (
            SECURITY_NOTIFICATIONS_PAGE_SIZE, security_notification_event_types, security_notifications_queryset,
        )
        severity = _param(request, 'severity', 20)
        event_type = _param(request, 'event_type', 100)
        page, meta = _paged(security_notifications_queryset(severity, event_type).select_related('user'),
                            SECURITY_NOTIFICATIONS_PAGE_SIZE, request.query_params.get('page'))
        body = {
            'notifications': [{
                'id': n.pk, 'severity': n.severity, 'event_type': n.event_type, 'sent_at': _iso(n.sent_at),
                'details': n.details, 'ip_address': n.ip_address, 'member': _name(n.user),
                'email_sent': n.email_sent, 'email_sent_to': n.email_sent_to, 'email_error': n.email_error,
            } for n in page.object_list],
            **meta,
        }
        if page.number == 1:
            body['event_types'] = sorted(set(security_notification_event_types()))
        return Response(body)
