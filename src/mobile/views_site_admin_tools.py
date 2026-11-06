"""
Site admin in the app, part four: Admin v2's tool and report pages.

Same gate as the rest of Site admin (``src/mobile/site_admin.py``: allowlist →
404, per-device 12-hour unlock → 403 ``admin_locked``). None of these website
pages checks ``user.is_admin``, so neither does the app. Each endpoint runs the
website page's own code:

* Chapter health — ``cached_chapter_health`` (src/view/admin_v2_health.py),
  ``?days=30|60|90``, the same 2-minute cache.
* System tools — the cache scopes and jobs (``SCOPES``, ``NOT_OFFERED``,
  ``job_rows``), the redacted error lines and slow pages, and the two
  actions through ``clear_cache_scope`` / ``run_job_now`` (src/view/
  admin_v2_system.py: the same refusals, one-a-minute limit and
  ``settings_changed`` rows). Like the page's confirm panel, both need
  ``confirm: true``; without it: 400 ``confirm_required``.
* Scheduled jobs — ``celery_overview`` (the 1-second worker ping).
* Backups — ``backups_overview`` (names, sizes and dates only).
* Permissions map — ``permissions_map`` (read only, as on the website).
* Push composer — ``push_preview`` and ``send_previewed_push`` (src/view/
  admin_v2_push.py): preview first, then send with the preview's signed
  token (this admin's, 15 minutes, once), within the master switch and the
  5-an-hour limit; the same ActivityLog row.
* Push subscriptions (browsers) — the page's list, and
  ``delete_push_subscription_row`` / ``clear_all_push_subscriptions``
  (``confirm: true`` for clearing them all).
* Notifications dashboard and logs — ``notification_dashboard_stats``,
  ``filter_notification_logs`` / ``notification_log_stats`` (50 a page) and
  one log.
"""
import datetime
from decimal import Decimal

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView
from src.mobile.views_site_admin import BadRequest, _http, _iso, _person

NOTIFICATION_LOG_PAGE = 50


def plain(value):
    """Dates, times and Decimals → JSON-friendly, recursively (lists, tuples, dicts, sets)."""
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v) for v in value]
    return value


def _confirmed(request):
    data = request.data if hasattr(request.data, 'get') else {}
    if data.get('confirm') is not True:
        raise BadRequest('Confirm it first.', code='confirm_required')
    return data


def _level(level, message):
    """A shared helper's (level, message) → 200 for success/warning, 400 for error."""
    if level == 'error':
        raise BadRequest(message, code='refused')
    return {'ok': True, 'level': level, 'message': message}


# ── Chapter health ───────────────────────────────────────────────────────────

class ChapterHealthView(SiteAdminAPIView):
    """GET ``?days=30|60|90``: the Chapter health page."""
    web_page = 'admin_v2_health'

    def get(self, request):
        from src.view.admin_v2_health import CACHE_SECONDS, DAY_OPTIONS, cached_chapter_health, clean_days
        days = clean_days(request.query_params.get('days'))
        return Response({'days': days, 'day_options': list(DAY_OPTIONS), 'cache_seconds': CACHE_SECONDS,
                         **plain(cached_chapter_health(days))})


# ── System tools ─────────────────────────────────────────────────────────────

def _job(row):
    t = row['task']
    return {'id': t.pk, 'name': t.name, 'task': t.task, 'enabled': t.enabled, 'status': row['status'],
            'next_due': _iso(row.get('next_due')), 'last_run_at': _iso(t.last_run_at),
            'total_run_count': t.total_run_count, 'refusal': row.get('refusal'),
            'schedule': row.get('schedule_str')}


class SystemView(SiteAdminAPIView):
    """GET: the System tools page (cache scopes, jobs, recent errors, slow pages)."""
    web_page = 'admin_v2_system'

    def get(self, request):
        import os

        from src.view import admin_v2_system as system
        errors, problem = system.read_errors()
        slow, perf, slow_ms = system.slow_pages()
        return Response({
            'scopes': [{'key': k, 'label': label, 'help': help_} for k, (label, help_, _fn) in system.SCOPES.items()],
            'not_offered': [{'label': label, 'why': why} for label, why in system.NOT_OFFERED],
            'jobs': [_job(r) for r in system.job_rows()],
            'errors': errors,
            'errors_problem': problem,
            'log_name': os.path.basename(system.log_file_path() or ''),
            'slow': plain(slow),
            'slow_ms': slow_ms,
            'sample_rate': plain(perf.get('sample_rate')) if isinstance(perf, dict) else None,
        })


class SystemClearCacheView(SiteAdminAPIView):
    """POST ``{scope, confirm: true}``: ``clear_cache_scope``."""
    web_page = 'admin_v2_system_clear_cache'

    def post(self, request):
        from src.view.admin_v2_system import SCOPES, clear_cache_scope
        data = request.data if hasattr(request.data, 'get') else {}
        scope = data.get('scope')
        if not isinstance(scope, str) or scope not in SCOPES:
            raise BadRequest('Unknown cache scope.', code='invalid_input')
        _confirmed(request)
        ok, message = clear_cache_scope(_http(request), scope)
        return Response(_level('success' if ok else 'error', message))


class SystemRunJobView(SiteAdminAPIView):
    """POST ``{task, confirm: true}``: ``run_job_now`` (refused jobs → 400 ``refused``)."""
    web_page = 'admin_v2_system_run_job'

    def post(self, request):
        from django_celery_beat.models import PeriodicTask

        from src.view.admin_v2_system import run_job_now
        data = request.data if hasattr(request.data, 'get') else {}
        raw = data.get('task')
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise BadRequest('Choose a scheduled job.', code='invalid_input')
        task = get_object_or_404(PeriodicTask, pk=raw)
        _confirmed(request)
        return Response(_level(*run_job_now(_http(request), task)))


class CeleryView(SiteAdminAPIView):
    """GET: the Scheduled jobs page (workers and every periodic task)."""
    web_page = 'admin_v2_celery_health'

    def get(self, request):
        from src.view.admin_v2 import celery_overview
        from src.view.admin_v2_system import job_refusal
        data = celery_overview()
        return Response({
            'workers_up': data['workers_up'],
            'workers': [w['name'] for w in data['worker_details']],
            'tasks': [_job({**r, 'refusal': job_refusal(r['task'])}) for r in data['task_rows']],
        })


class BackupsView(SiteAdminAPIView):
    """GET: the Backups page (file names, sizes and dates; the verify drills)."""
    web_page = 'admin_v2_backups'

    def get(self, request):
        from src.view.admin_v2_ops import backups_overview
        d = backups_overview()

        def drill(row):
            return {'id': row.pk, 'event_type': row.event_type, 'ok': row.event_type == 'BACKUP_VERIFIED',
                    'details': (row.details or '')[:1000], 'sent_at': _iso(row.sent_at)} if row else None

        return Response({
            'directory': str(d['directory']),
            'error': d['error'],
            'files': plain(d['files']),
            'file_count': d['file_count'],
            'newest': plain(d['newest']),
            'stale': bool(d['stale']),
            'failing': d['failing'],
            'last_ok': drill(d['last_ok']),
            'last_fail': drill(d['last_fail']),
            'drills': [drill(r) for r in d['drills']],
        })


# ── Permissions map ──────────────────────────────────────────────────────────

class PermissionsView(SiteAdminAPIView):
    """GET ``?q=&flagged=1``: the Permissions map (read only)."""
    web_page = 'admin_v2_permissions'

    def get(self, request):
        from src.view.admin_v2_permissions import STALE_DAYS, permissions_map
        q = (request.query_params.get('q') or '')[:100]
        flagged = request.query_params.get('flagged') == '1'
        data = permissions_map(q, flagged)

        def entry(e):
            m = e['member']
            return {'member': _person(m), 'member_type': m.member_type, 'member_status': m.member_status,
                    'detail': e['detail'], 'flags': e['flags'], 'flag_labels': e['flag_labels'],
                    'last_sign_in': _iso(e['last_sign_in'])}

        return Response({
            'q': q, 'flagged': flagged, 'stale_days': STALE_DAYS,
            'sections': [{
                'key': s['key'], 'title': s['title'], 'about': s['about'], 'n': s['n'],
                'groups': [{'label': g['label'], 'flags': g['flags'], 'flag_labels': g['flag_labels'],
                            'entries': [entry(e) for e in g['entries']]} for g in s['groups']],
            } for s in data['sections']],
            'flag_counts': data['flag_counts'],
            'total_flags': data['total_flags'],
            'unknown_allowlist': data['unknown_allowlist'],
        })



# ── Push composer ────────────────────────────────────────────────────────────

class PushView(SiteAdminAPIView):
    """GET: what the composer offers (audiences, committees, members, limits, recent sends)."""
    web_page = 'admin_v2_push'

    def get(self, request):
        from src.models import Committee
        from src.view import admin_v2_push as push
        recent = []
        for row in push.recent_sends():
            meta = row.metadata or {}
            recent.append({'id': row.pk, 'by': _person(row.user), 'at': _iso(row.timestamp),
                           'title': meta.get('title') or row.object_repr, 'body': meta.get('body', ''),
                           'audience_label': meta.get('audience_label', ''), 'n': meta.get('n'),
                           'link': meta.get('link', '')})
        return Response({
            'audiences': [{'value': v, 'label': label} for v, label in push.AUDIENCES],
            'member_types': list(push.MEMBER_TYPES),
            'statuses': list(push.STATUSES),
            'committees': [{'id': c.pk, 'name': c.name} for c in Committee.objects.filter(
                is_active=True, is_archived=False).order_by('name').only('pk', 'name')],
            'members': [_person(m) for m in push._reachable_members().order_by('name').only(
                'user_id', 'name', 'preferred_name')],
            'title_max': push.TITLE_MAX, 'body_max': push.BODY_MAX, 'link_max': push.LINK_MAX,
            'rate_limit': push.RATE_LIMIT, 'sends_left': push.sends_left(request.user),
            'master_on': push.push_master_on(),
            'recent': recent,
        })


def _push_form(data):
    """The app's JSON → the composer form's strings (committee as text, as the page posts it)."""
    if not hasattr(data, 'get'):
        return {}
    out = {}
    for key in ('audience', 'committee', 'member_type', 'member_status', 'user_id', 'title', 'body', 'link'):
        value = data.get(key)
        if isinstance(value, bool):
            value = ''
        out[key] = str(value) if isinstance(value, (str, int)) else ''
    return out


class PushPreviewView(SiteAdminAPIView):
    """POST the form: ``push_preview`` (sends nothing). 400 ``invalid_input`` with the page's message."""
    web_page = 'admin_v2_push'

    def post(self, request):
        from src.view.admin_v2_push import PushFormError, push_master_on, push_preview, sends_left
        try:
            preview = push_preview(request.user, _push_form(request.data))
        except PushFormError as exc:
            raise BadRequest(str(exc), code='invalid_input')
        return Response({'preview': preview, 'sends_left': sends_left(request.user),
                         'master_on': push_master_on()})


class PushSendView(SiteAdminAPIView):
    """POST ``{token}`` from the preview: ``send_previewed_push``. Refusals → 400 ``refused``."""
    web_page = 'admin_v2_push_send'

    def post(self, request):
        from src.view.admin_v2_push import send_previewed_push
        data = request.data if hasattr(request.data, 'get') else {}
        token = data.get('token')
        level, message, n = send_previewed_push(_http(request), token if isinstance(token, str) else '')
        return Response({**_level(level, message), 'n': n})


# ── Push subscriptions (browsers) ────────────────────────────────────────────

class PushSubscriptionsView(SiteAdminAPIView):
    """GET: every browser push subscription, by member (the Push subscriptions page)."""
    web_page = 'admin_v2_push_subscriptions'

    def get(self, request):
        from src.models import PushSubscription
        from src.models.users import member_defer
        subs = (PushSubscription.objects.select_related('user').defer(*member_defer('user'))
                .order_by('user__name', '-created_at'))
        groups = []
        for s in subs:
            if not groups or groups[-1]['member']['user_id'] != str(s.user.user_id):
                groups.append({'member': _person(s.user), 'subscriptions': []})
            groups[-1]['subscriptions'].append({
                'id': s.pk, 'user_agent': s.user_agent, 'created_at': _iso(s.created_at),
                'last_used_at': _iso(s.last_used_at),
                # The host only: the endpoint itself is the push address.
                'service': s.endpoint.split('/')[2] if s.endpoint.count('/') >= 2 else '',
            })
        return Response({'members': groups, 'total': sum(len(g['subscriptions']) for g in groups)})


class PushSubscriptionDeleteView(SiteAdminAPIView):
    """POST: ``delete_push_subscription_row``."""
    web_page = 'admin_v2_delete_push_subscription'

    def post(self, request, sub_id):
        from src.models import PushSubscription
        from src.view.admin_v2 import delete_push_subscription_row
        sub = get_object_or_404(PushSubscription.objects.select_related('user'), pk=sub_id)
        who = delete_push_subscription_row(_http(request), request.user, sub)
        return Response({'ok': True, 'message': f'Deleted push subscription for {who}.'})


class PushSubscriptionsClearView(SiteAdminAPIView):
    """POST ``{confirm: true}``: ``clear_all_push_subscriptions``."""
    web_page = 'admin_v2_clear_push_subscriptions'

    def post(self, request):
        from src.view.admin_v2 import clear_all_push_subscriptions
        _confirmed(request)
        count = clear_all_push_subscriptions(_http(request), request.user)
        return Response({'ok': True, 'count': count,
                         'message': f'Cleared {count} push subscription(s). Users will need to re-subscribe.'})


# ── Notifications dashboard and logs ─────────────────────────────────────────

def _log_row(log):
    return {
        'id': log.pk, 'schedule_id': log.schedule_id, 'notification_type': log.notification_type,
        'title': log.title, 'status': log.status, 'status_label': log.get_status_display(),
        'sent_via_email': log.sent_via_email, 'sent_via_in_app': log.sent_via_in_app,
        'recipient_count': log.recipient_count, 'successful_count': log.successful_count,
        'failed_count': log.failed_count, 'created_at': _iso(log.created_at), 'sent_at': _iso(log.sent_at),
    }


class NotificationsView(SiteAdminAPIView):
    """GET: the Notifications dashboard (four numbers, the 20 newest logs)."""
    web_page = 'admin_v2_notifications'

    def get(self, request):
        from src.models import NotificationLog
        from src.view.notification_admin import notification_dashboard_stats
        return Response({'stats': notification_dashboard_stats(),
                         'recent': [_log_row(x) for x in NotificationLog.objects.all()[:20]]})


class NotificationLogsView(SiteAdminAPIView):
    """GET ``?status=&type=&from=YYYY-MM-DD&to=&page=``: the Notification logs page, 50 a page."""
    web_page = 'admin_v2_notification_logs'

    def get(self, request):
        from src.models import NotificationLog, NotificationSchedule
        from src.view.notification_admin import filter_notification_logs, notification_log_stats
        p = request.query_params
        status = (p.get('status') or '').strip()[:20]
        kind = (p.get('type') or '').strip()[:50]
        dates = {}
        for key in ('from', 'to'):
            raw = (p.get(key) or '').strip()
            if raw:
                try:
                    parsed = parse_date(raw)
                except ValueError:
                    parsed = None
                if parsed is None:
                    raise BadRequest('Dates look like 2026-11-06.', code='invalid_input')
                dates[key] = parsed
        logs = filter_notification_logs(NotificationLog.objects.all(), status, kind,
                                        dates.get('from'), dates.get('to'))
        page = Paginator(logs, NOTIFICATION_LOG_PAGE).get_page(p.get('page'))
        return Response({
            'logs': [_log_row(x) for x in page.object_list],
            'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
            'has_next': page.has_next(),
            'stats': notification_log_stats(),
            'status_choices': [{'value': v, 'label': label} for v, label in NotificationLog.STATUS_CHOICES],
            'type_choices': [{'value': v, 'label': label}
                             for v, label in NotificationSchedule.NOTIFICATION_TYPE_CHOICES],
        })


class NotificationLogDetailView(SiteAdminAPIView):
    """GET: one notification log (the log detail page)."""
    web_page = 'admin_v2_notification_log_detail'

    def get(self, request, log_id):
        from src.models import NotificationLog
        log = get_object_or_404(NotificationLog.objects.select_related('schedule'), pk=log_id)
        return Response({**_log_row(log), 'message': log.message, 'error_message': log.error_message,
                         'schedule_name': log.schedule.name if log.schedule else None,
                         'related_object_type': log.related_object_type,
                         'related_object_id': log.related_object_id,
                         'scheduled_for': _iso(log.scheduled_for)})
