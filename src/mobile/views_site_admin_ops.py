"""
Site admin in the app, part two (v4.4.4): IP lists, exports, page visits,
trends and account deletions.

Same gate as the rest of Site admin (``src/mobile/site_admin.py``: allowlist →
404, per-device 12-hour unlock → 403 ``admin_locked``). None of these website
pages also checks ``user.is_admin``, so neither does the app. Each endpoint
runs the website view's own code:

* IP allow and block lists — ``add_ip_whitelist_entry`` /
  ``remove_ip_whitelist_entry`` / ``add_ip_blacklist_entry`` /
  ``remove_ip_blacklist_entry`` (src/view/admin_v2.py): the page's checks,
  rows, cache keys and ActivityLog entries. Refusals are 400 with the page's
  message (``required`` / ``invalid``) or 409 ``exists``.
* Exports — ``members_csv`` / ``attendance_csv`` / ``service_csv``
  (src/view/admin_v2_exports.py): the same file and the same ActivityLog
  row. The website refuses these three to a session signed in from abroad
  (``GeoRestrictionMiddleware``); the app refuses them to an install that
  signed in from abroad (``MobileDevice.login_geo_suspicious``, recorded from
  the same sign-in pipeline), with the same ``GEO_BLOCK`` log line. 403
  ``geo_restricted``.
* Page visits — ``page_visits_data`` (the dashboard and its drill-down).
* Trends — ``trend_series``.
* Account deletions — ``account_deletions_data``, and Restore / Rebuild
  through ``src.account_deletion`` (``restore_account`` /
  ``rebuild_account``), which write the AdminActionLog rows and send the
  emails. Refusals are 400 with ``DeletionRefused``'s code and message.
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView
from src.mobile.views_site_admin import BadRequest, _http, _iso, _person, _text
from src.models import ParliamentUser

geo_logger = logging.getLogger('admin_actions')

PAGE_VISITS_LIMIT = 200


class Conflict(exceptions.APIException):
    status_code = 409


def _ip_outcome(outcome):
    code, message = outcome
    if code == 'exists':
        raise Conflict(message, code='exists')
    if code != 'ok':
        raise BadRequest(message, code=code)
    return message


# ── IP lists ─────────────────────────────────────────────────────────────────

def _whitelist_entry(e):
    return {'ip_address': e.ip_address, 'description': e.description, 'added_by': _person(e.added_by),
            'added_at': _iso(e.added_at)}


def _blacklist_entry(e):
    return {'ip_address': e.ip_address, 'reason': e.reason, 'added_by': _person(e.added_by),
            'added_at': _iso(e.added_at), 'expires_at': _iso(e.expires_at), 'block_count': e.block_count,
            'last_blocked': _iso(e.last_blocked)}


def _current_ip(request):
    from src.utils.security_utils import get_client_ip
    try:
        return get_client_ip(_http(request))
    except Exception:
        return None


class IPWhitelistView(SiteAdminAPIView):
    """GET: the active whitelist, newest first (the IP Whitelist page)."""
    web_page = 'admin_v2_ip_whitelist'

    def get(self, request):
        from src.models import IPWhitelist
        entries = IPWhitelist.objects.filter(is_active=True).select_related('added_by').order_by('-added_at')
        return Response({'entries': [_whitelist_entry(e) for e in entries],
                         'current_ip': _current_ip(request)})


class IPWhitelistAddView(SiteAdminAPIView):
    """POST ``{ip_address, description}`` (``add_ip_whitelist_entry``)."""
    web_page = 'admin_v2_add_ip_whitelist'

    def post(self, request):
        from src.view.admin_v2 import add_ip_whitelist_entry
        message = _ip_outcome(add_ip_whitelist_entry(
            _http(request), request.user, _text(request.data, 'ip_address', 100),
            _text(request.data, 'description', 500)))
        return Response({'ok': True, 'message': message})


class IPWhitelistRemoveView(SiteAdminAPIView):
    """POST ``{ip_address}`` (``remove_ip_whitelist_entry``)."""
    web_page = 'admin_v2_remove_ip_whitelist'

    def post(self, request):
        from src.view.admin_v2 import remove_ip_whitelist_entry
        message = _ip_outcome(remove_ip_whitelist_entry(
            _http(request), request.user, _text(request.data, 'ip_address', 100)))
        return Response({'ok': True, 'message': message})


class IPBlacklistView(SiteAdminAPIView):
    """GET: the active blacklist, newest first (the IP Blacklist page)."""
    web_page = 'admin_v2_ip_blacklist'

    def get(self, request):
        from src.models import IPBlacklist
        entries = IPBlacklist.objects.filter(is_active=True).select_related('added_by').order_by('-added_at')
        return Response({'entries': [_blacklist_entry(e) for e in entries],
                         'current_ip': _current_ip(request)})


class IPBlacklistAddView(SiteAdminAPIView):
    """POST ``{ip_address, reason}`` (``add_ip_blacklist_entry``)."""
    web_page = 'admin_v2_add_ip_blacklist'

    def post(self, request):
        from src.view.admin_v2 import add_ip_blacklist_entry
        message = _ip_outcome(add_ip_blacklist_entry(
            _http(request), request.user, _text(request.data, 'ip_address', 100),
            _text(request.data, 'reason', 500)))
        return Response({'ok': True, 'message': message})


class IPBlacklistRemoveView(SiteAdminAPIView):
    """POST ``{ip_address}`` (``remove_ip_blacklist_entry``)."""
    web_page = 'admin_v2_remove_ip_blacklist'

    def post(self, request):
        from src.view.admin_v2 import remove_ip_blacklist_entry
        message = _ip_outcome(remove_ip_blacklist_entry(
            _http(request), request.user, _text(request.data, 'ip_address', 100)))
        return Response({'ok': True, 'message': message})


# ── Exports ──────────────────────────────────────────────────────────────────

class ExportsView(SiteAdminAPIView):
    """GET: what the Exports page offers (the member statuses and the 8 latest service periods)."""
    web_page = 'admin_v2_exports'

    def get(self, request):
        from src.models import ServicePeriod
        from src.view.admin_v2_exports import STATUSES
        periods = ServicePeriod.objects.order_by('-start_date')[:8]
        return Response({
            'statuses': list(STATUSES),
            'periods': [{'id': p.pk, 'name': p.name, 'is_active': bool(p.is_active)} for p in periods],
            'geo_restricted': bool(getattr(request.auth, 'login_geo_suspicious', False)),
        })


class _ExportView(SiteAdminAPIView):
    """A CSV from the website's export function, refused to an install signed in from abroad."""
    csv = None   # name of the function in src.view.admin_v2_exports

    def get(self, request):
        from src.view import admin_v2_exports
        if getattr(request.auth, 'login_geo_suspicious', False):
            geo = getattr(request.auth, 'login_geo', None) or {}
            country = geo.get('country') or 'Unknown'
            location = f"{geo['city']}, {country}" if geo.get('city') else country
            geo_logger.warning(
                f"GEO_BLOCK: User '{request.user.user_id}' attempted to access "
                f"restricted export '{request.path}' (view={self.web_page}) "
                f"from non-US location ({location}). Blocked."
            )
            raise exceptions.PermissionDenied(
                'Exports are not available on a sign-in from outside the United States.', code='geo_restricted')
        return getattr(admin_v2_exports, self.csv)(_http(request), request.query_params)


class ExportMembersView(_ExportView):
    """GET ``?status=``: the member roster CSV (``members_csv``)."""
    web_page = 'admin_v2_export_members'
    csv = 'members_csv'


class ExportAttendanceView(_ExportView):
    """GET ``?status=&days=``: attendance per member (``attendance_csv``)."""
    web_page = 'admin_v2_export_attendance'
    csv = 'attendance_csv'


class ExportServiceView(_ExportView):
    """GET ``?status=&period=``: service hours per member (``service_csv``)."""
    web_page = 'admin_v2_export_service'
    csv = 'service_csv'


# ── Page visits and trends ───────────────────────────────────────────────────

class PageVisitsView(SiteAdminAPIView):
    """
    GET ``?sort=total|users&user=&status=``: visits per path; with ``?path=``,
    who visited it (``page_visits_data``). At most 200 rows; ``total`` counts all.
    """
    web_page = 'admin_v2_page_visits'

    def get(self, request):
        from src.view.admin_v2 import page_visits_data
        q = request.query_params
        sort = 'users' if q.get('sort') == 'users' else 'total'
        data = page_visits_data(sort=sort, drill_path=(q.get('path') or '')[:255],
                                user_filter=(q.get('user') or '')[:100], status_filter=(q.get('status') or '')[:20])
        body = {'sort': sort, 'user': data['user_filter'], 'status': data['status_filter'],
                'statuses': list(data['status_choices']), 'path': data['drill_path']}
        if data['drill_path']:
            rows = data['rows']
            body.update({
                'total': rows.count(),
                'visitors': [{'member': _person(r.user), 'member_type': r.user.member_type or None,
                              'count': r.count} for r in rows[:PAGE_VISITS_LIMIT]],
            })
        else:
            pages = data['pages']
            body.update({
                'total': pages.count(),
                'pages': [{'path': p['path'], 'total': p['total'] or 0, 'unique_users': p['unique_users']}
                          for p in pages[:PAGE_VISITS_LIMIT]],
                'matched_users': None if data['matched_users'] is None
                else [_person(u) for u in data['matched_users']],
            })
        return Response(body)


class TrendsView(SiteAdminAPIView):
    """GET: the Trends page's three weekly series, 12 weeks (``trend_series``)."""
    web_page = 'admin_v2_trends'

    def get(self, request):
        from src.view.admin_v2_trends import trend_series
        return Response(trend_series())


# ── Account deletions ────────────────────────────────────────────────────────

class AccountDeletionsView(SiteAdminAPIView):
    """GET: pending deletions and erased accounts (``account_deletions_data``)."""
    web_page = 'admin_v2_account_deletions'

    def get(self, request):
        from src.view.account_deletion import account_deletions_data
        data = account_deletions_data()

        def member(m):
            return {'user_id': str(m.user_id), 'name': m.get_display_name(), 'role_number': m.role_number,
                    'pledge_class': m.pledge_class or None, 'member_status': m.member_status,
                    'requested_at': _iso(m.deletion_requested_at),
                    'requested_via': m.deletion_requested_via or None}

        return Response({
            'purge_days': data['purge_days'],
            'pending': [{**member(row['member']), 'purge_on': row['purge_on'].isoformat()
                         if row['purge_on'] else None} for row in data['pending']],
            'purged': [{**member(m), 'erased_at': _iso(m.account_deleted_at)} for m in data['purged']],
        })


def _refused(exc):
    return BadRequest(exc.message, code=exc.code)


class AccountRestoreView(SiteAdminAPIView):
    """POST: restore a pending deletion at the member's request (``restore_account``)."""
    web_page = 'admin_v2_restore_account'

    def post(self, request, user_id):
        from src import account_deletion as deletion
        member = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            deletion.restore_account(member, request.user, request=_http(request))
        except deletion.DeletionRefused as exc:
            raise _refused(exc)
        return Response({'ok': True, 'message': f'Restored {member.get_display_name()}\'s account. They set a '
                                                f'new password with "Forgot password" to sign in.'})


class AccountRebuildView(SiteAdminAPIView):
    """POST ``{email, username}``: rebuild an erased account at the member's request (``rebuild_account``)."""
    web_page = 'admin_v2_rebuild_account'

    def post(self, request, user_id):
        from src import account_deletion as deletion
        member = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            deletion.rebuild_account(member, request.user, _text(request.data, 'email', 254),
                                     _text(request.data, 'username', 150), request=_http(request))
        except deletion.DeletionRefused as exc:
            raise _refused(exc)
        return Response({'ok': True, 'message': f'Rebuilt an account for {member.get_display_name()}. A '
                                                f'set-password link was emailed to {member.email}.'})
