"""
Site admin in the app, part five: Admin v2's security pages and the 2FA policy.

Same gate as the rest of Site admin (``src/mobile/site_admin.py``). The
Security dashboard, Quarantine, Honeypot logs and CSP violations pages also
check ``user.is_admin`` on the website, so these endpoints do too
(``needs_is_admin``: 403 ``admin_required``). Each runs the page's own code
(src/view/admin_v2.py):

* Security dashboard — ``security_overview``.
* Quarantine — ``active_quarantines_list`` and the released ones (50), and
  ``release_quarantine`` / ``quarantine_member`` (the same AdminActionLog
  rows and security alert).
* Honeypot logs — ``honeypot_log_data`` (50 a page, ``?endpoint=&ip=``), one
  row deleted, ``clear_honeypot_entries`` (one address, or all with
  ``confirm: true``) and ``blacklist_honeypot_ips``.
* CSP violations — ``csp_groups`` and ``dismiss_csp_group``.
* Two-factor — the 2FA page (src/view/admin_two_factor.py):
  ``two_factor_members`` / ``filter_two_factor_members`` and
  ``set_two_factor_policy`` (the same `other` row). No ``is_admin`` check:
  the website's 2FA page has none. A member's own requirement and reset are
  the member screen's (views_site_admin.py).
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView
from src.mobile.views_site_admin import BadRequest, _http, _iso, _person, _text

RELEASED_LIMIT = 50


def _quarantine(q):
    return {
        'id': q.pk, 'member': _person(q.user), 'ip_address': q.ip_address, 'reason': q.reason,
        'is_auto': q.is_auto, 'quarantined_at': _iso(q.quarantined_at), 'quarantined_by': _person(q.quarantined_by),
        'expires_at': _iso(q.expires_at), 'released_at': _iso(q.released_at),
        'released_by': _person(getattr(q, 'released_by', None)) if q.released_at else None,
        'release_notes': q.release_notes,
    }


def _honeypot(h, blocked):
    return {
        'id': h.pk, 'endpoint': h.endpoint, 'ip_address': h.ip_address, 'blocked': h.ip_address in blocked,
        'user_agent': (h.user_agent or '')[:300], 'request_method': h.request_method,
        'action_taken': h.action_taken, 'accessed_at': _iso(h.accessed_at),
    }


class _AdminOnly(SiteAdminAPIView):
    needs_is_admin = True


# ── Security dashboard ───────────────────────────────────────────────────────

class SecurityView(_AdminOnly):
    """GET: the Security dashboard."""
    web_page = 'admin_v2_security'

    def get(self, request):
        from src.view.admin_v2 import security_overview
        d = security_overview()
        blocked = d['blacklisted_ips']
        lockdown = d['lockdown']
        return Response({
            'counts': {
                'quarantined': len(d['active_quarantines']), 'honeypot_24h': d['honeypot_24h'],
                'critical_notifications_24h': d['critical_notifications'],
                'active_lockouts': d['active_lockouts_count'], 'new_alerts': d['new_alerts_count'],
                'critical_alerts': d['critical_alerts_count'], 'blocked_ips': d['blacklist_count'],
                'allowed_ips': d['whitelist_count'], 'csp_violations': d['csp_violation_count'],
            },
            'lockdown': {'active': lockdown.is_active, 'reason': lockdown.reason if lockdown.is_active else ''},
            'quarantines': [_quarantine(q) for q in d['active_quarantines'][:10]],
            'honeypot': [_honeypot(h, blocked) for h in d['recent_honeypot']],
            'notifications': [{'id': n.pk, 'event_type': n.event_type, 'severity': n.severity,
                               'sent_at': _iso(n.sent_at), 'email_sent': n.email_sent}
                              for n in d['recent_notifications']],
            'alerts': [{'id': a.pk, 'member': _person(a.user), 'title': a.title, 'alert_type': a.alert_type,
                        'severity': a.severity, 'created_at': _iso(a.created_at)} for a in d['new_alerts']],
        })


# ── Quarantine ───────────────────────────────────────────────────────────────

class QuarantineView(_AdminOnly):
    """GET: active quarantines and the 50 newest released ones."""
    web_page = 'admin_v2_quarantine'

    def get(self, request):
        from src.models import QuarantinedAccount
        from src.models.users import member_defer
        from src.view.admin_v2 import active_quarantines_list
        released = (QuarantinedAccount.objects.filter(released_at__isnull=False)
                    .select_related('user', 'quarantined_by', 'released_by')
                    .defer(*member_defer('user', 'quarantined_by', 'released_by'))
                    .order_by('-released_at')[:RELEASED_LIMIT])
        return Response({'active': [_quarantine(q) for q in active_quarantines_list()],
                         'released': [_quarantine(q) for q in released]})


class QuarantineReleaseView(_AdminOnly):
    """POST ``{notes}``: ``release_quarantine``."""
    web_page = 'admin_v2_quarantine'

    def post(self, request, quarantine_id):
        from src.models import QuarantinedAccount
        from src.view.admin_v2 import release_quarantine
        q = get_object_or_404(QuarantinedAccount.objects.select_related('user'), pk=quarantine_id)
        if q.released_at:
            raise BadRequest('That quarantine was already released.', code='invalid_input')
        release_quarantine(_http(request), request.user, q, _text(request.data, 'notes', 2000))
        return Response({'ok': True, 'message': f'Released quarantine for {q.user.name}'})


class QuarantineAddView(_AdminOnly):
    """POST ``{user_id, reason, expires_at?}``: ``quarantine_member`` (with the page's placeholder IP)."""
    web_page = 'admin_v2_quarantine'

    def post(self, request):
        from src.models import ParliamentUser
        from src.view.admin_v2 import quarantine_member
        member = get_object_or_404(ParliamentUser, user_id=_text(request.data, 'user_id', 40))
        if member.is_quarantined:
            raise BadRequest(f'{member.name} is already quarantined.', code='invalid_input')
        reason = _text(request.data, 'reason', 2000) or 'Manual quarantine by admin'
        expires_at = None
        raw = _text(request.data, 'expires_at', 40)
        if raw:
            try:
                expires_at = parse_datetime(raw)
            except ValueError:
                expires_at = None
            if expires_at is None:
                raise BadRequest('The end time isn’t a date and time.', code='invalid_input')
            if timezone.is_naive(expires_at):
                expires_at = timezone.make_aware(expires_at)
            if expires_at <= timezone.now():
                raise BadRequest('The end time has to be in the future.', code='invalid_input')
        # The page's form sends this placeholder when no address is given.
        ip = '0.0.0.0'  # nosec B104  # placeholder address on the record, not a socket bind
        expiry = quarantine_member(_http(request), request.user, member, reason, ip, expires_at)
        return Response({'ok': True, 'message': f'Quarantined account: {member.name}{expiry}'})


# ── Honeypot ─────────────────────────────────────────────────────────────────

class HoneypotView(_AdminOnly):
    """GET ``?endpoint=&ip=&page=``: the Honeypot logs page."""
    web_page = 'admin_v2_honeypot_logs'

    def get(self, request):
        from src.view.admin_v2 import honeypot_log_data
        p = request.query_params
        d = honeypot_log_data((p.get('endpoint') or '')[:200], (p.get('ip') or '').strip()[:45], p.get('page'))
        page, blocked = d['logs'], d['blacklisted_ips']
        return Response({
            'logs': [_honeypot(h, blocked) for h in page.object_list],
            'page': page.number, 'pages': page.paginator.num_pages, 'has_next': page.has_next(),
            'total_filtered': d['total_filtered'], 'total_24h': d['total_24h'], 'total_all': d['total_all'],
            'top_endpoints': list(d['top_endpoints']),
            'top_ips': [{**r, 'blocked': r['ip_address'] in blocked} for r in d['top_ips']],
        })


class HoneypotDeleteView(_AdminOnly):
    """POST: delete one honeypot row (the page's delete)."""
    web_page = 'admin_v2_delete_honeypot_log'

    def post(self, request, log_id):
        from src.models import HoneypotAccess
        get_object_or_404(HoneypotAccess, pk=log_id).delete()
        return Response({'ok': True})


class HoneypotClearView(_AdminOnly):
    """POST ``{ip_address}`` (that address's rows) or ``{confirm: true}`` (all of them)."""
    web_page = 'admin_v2_clear_honeypot_logs'

    def post(self, request):
        from src.view.admin_v2 import clear_honeypot_entries
        ip = _text(request.data, 'ip_address', 45)
        if not ip and (not hasattr(request.data, 'get') or request.data.get('confirm') is not True):
            raise BadRequest('Confirm it first.', code='confirm_required')
        deleted = clear_honeypot_entries(ip)
        plural = 'ies' if deleted != 1 else 'y'
        if ip:
            message = f'Deleted {deleted} log entr{plural} for {ip}.' if deleted else f'No logs found for {ip}.'
        else:
            message = f'Cleared {deleted} honeypot log entr{plural}.' if deleted else 'No honeypot logs to clear.'
        return Response({'ok': True, 'deleted': deleted, 'message': message})


class HoneypotBlacklistAllView(_AdminOnly):
    """POST: ``blacklist_honeypot_ips``."""
    web_page = 'admin_v2_blacklist_all_honeypot_ips'

    def post(self, request):
        from src.view.admin_v2 import blacklist_honeypot_ips
        created = blacklist_honeypot_ips(_http(request), request.user)
        message = (f'{created} IP address{"es" if created != 1 else ""} blacklisted.' if created
                   else 'All honeypot IPs are already blacklisted.')
        return Response({'ok': True, 'created': created, 'message': message})


# ── CSP violations ───────────────────────────────────────────────────────────

class CSPView(_AdminOnly):
    """GET ``?show_dismissed=1``: CSP reports grouped by directive and blocked address."""
    web_page = 'admin_v2_csp_violations'

    def get(self, request):
        from src.models import CSPViolation
        from src.view.admin_v2 import csp_groups
        show = request.query_params.get('show_dismissed') == '1'
        groups = [{'violated_directive': g['violated_directive'], 'blocked_uri': g['blocked_uri'],
                   'count': g['count'], 'latest': _iso(g['latest'])} for g in csp_groups(show)[:200]]
        return Response({'groups': groups, 'show_dismissed': show,
                         'total_undismissed': CSPViolation.objects.filter(dismissed=False).count()})


class CSPDismissView(_AdminOnly):
    """POST ``{violated_directive, blocked_uri}``: ``dismiss_csp_group``."""
    web_page = 'admin_v2_csp_violation_dismiss'

    def post(self, request):
        from src.view.admin_v2 import dismiss_csp_group
        data = request.data if hasattr(request.data, 'get') else {}
        directive, blocked = data.get('violated_directive', ''), data.get('blocked_uri', '')
        if not isinstance(directive, str) or not isinstance(blocked, str) or not (directive or blocked):
            raise BadRequest('Missing parameters', code='invalid_input')
        return Response({'ok': True, 'dismissed': dismiss_csp_group(request.user, directive, blocked)})


# ── Two-factor ───────────────────────────────────────────────────────────────

TWO_FACTOR_FILTERS = ('all', 'has_2fa', 'no_2fa', 'required', 'exempt', 'needs_setup')


class TwoFactorView(SiteAdminAPIView):
    """GET ``?filter=&search=``: the 2FA page (policy, numbers, members)."""
    web_page = 'admin_v2_two_factor'

    def get(self, request):
        from src.models_feature_flags import SiteSetting
        from src.view.admin_two_factor import (
            TWO_FACTOR_POLICY_OPTIONS, filter_two_factor_members, two_factor_members,
        )
        policy = SiteSetting.get_setting('2fa_policy_mode', 'none')
        rows, stats = two_factor_members(policy)
        filter_type = request.query_params.get('filter') or 'all'
        if filter_type not in TWO_FACTOR_FILTERS:
            filter_type = 'all'
        search = (request.query_params.get('search') or '').strip()[:100]
        rows = filter_two_factor_members(rows, filter_type, search)
        return Response({
            'policy': policy,
            'policy_options': [{'value': v, 'label': label} for v, label in TWO_FACTOR_POLICY_OPTIONS],
            'stats': stats, 'filter': filter_type, 'search': search,
            'members': [{'member': _person(r['user']), 'member_type': r['user'].member_type,
                         'has_2fa': r['has_2fa'], 'requirement': r['requirement_status'],
                         'requires_2fa': r['requires_2fa']} for r in rows],
        })


class TwoFactorPolicyView(SiteAdminAPIView):
    """POST ``{policy}``: ``set_two_factor_policy``. Already that policy → nothing changes."""
    web_page = 'admin_v2_two_factor_update_policy'

    def post(self, request):
        from src.models_feature_flags import SiteSetting
        from src.view.admin_two_factor import set_two_factor_policy
        policy = request.data.get('policy') if hasattr(request.data, 'get') else None
        if SiteSetting.get_setting('2fa_policy_mode', 'none') == policy:
            return Response({'ok': True, 'policy': policy, 'changed': False})
        if not isinstance(policy, str) or not set_two_factor_policy(_http(request), request.user, policy):
            raise BadRequest('Invalid policy', code='invalid_input')
        return Response({'ok': True, 'policy': policy, 'changed': True})
