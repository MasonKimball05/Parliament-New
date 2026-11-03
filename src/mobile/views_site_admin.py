"""
Site admin in the app (v4.4.3): the phone-useful parts of Admin v2.

Gate: ``src/mobile/site_admin.py`` (allowlist → 404, per-device 12-hour
unlock → 403 ``admin_locked``). Every action goes through the website view's
own helper, so it changes the same rows and writes the same log entries:

* home — the dashboard's key numbers, Celery health
  (``dashboard_celery_summary``), "needs attention" (``attention_items``),
  maintenance mode, lockdown (``activate_lockdown`` / ``deactivate_lockdown``),
  feature flags and page switches (``apply_feature_flag_toggle`` /
  ``apply_page_toggle``);
* members — search (``users_queryset``), the member page
  (``member_page_data``), login security (``login_security_data``), sign out
  everywhere / one device (``sign_member_out_everywhere`` /
  ``revoke_member_device``), release lockout (``release_member_lockouts``),
  force password reset (``perform_force_password_reset``), 2FA reset and
  requirement (``reset_member_2fa`` / ``set_member_2fa_requirement``);
* security — lockouts (``clear_lockout_row``), alerts
  (``dismiss_login_alert`` / ``dismiss_all_login_alerts``), the audit log
  (``audit_log_queryset``, read only);
* app installs — ``mobile_overview`` and ``send_test_push``.

Never sent to the phone: passwords (a forced reset's new password is emailed
or typed by the admin, and the alert note that stores it is withheld), push
tokens, hashes, environment values (setup checks are booleans, as on the
website). Kai rows are excluded from member activity as on the member page.
"""
import json
import uuid

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.site_admin import SiteAdminAPIView, lock_device, unlock_device, unlocked_until
from src.models import ActivityLog, LoginAlert, ParliamentUser

MEMBER_PAGE_SIZE = 30
AUDIT_PAGE_SIZE = 30
LIST_LIMIT = 100


class BadRequest(exceptions.APIException):
    """A 400 that keeps its message and code."""
    status_code = 400


def _iso(dt):
    return dt.isoformat() if dt else None


def _text(data, key, max_len=500):
    value = data.get(key, '') if hasattr(data, 'get') else ''
    return value.strip()[:max_len] if isinstance(value, str) else ''


def _http(request):
    """The Django request under DRF's, for the website helpers' log rows."""
    return getattr(request, '_request', request)


def _member(user_id):
    return get_object_or_404(ParliamentUser, user_id=user_id)


def _person(u):
    return {'user_id': str(u.user_id), 'name': u.get_display_name()} if u else None


def _status(device):
    until = unlocked_until(device)
    return {'unlocked': until is not None, 'unlocked_until': _iso(until)}


# ── Gate ─────────────────────────────────────────────────────────────────────

class StatusView(SiteAdminAPIView):
    """
    GET → ``{unlocked, unlocked_until, has_passkeys}`` for this install.
    ``has_passkeys`` (v4.4.3): whether to offer "Unlock with passkey", as the
    website's Admin v2 sign-in page decides.
    """
    requires_unlock = False
    web_page = 'admin_v2_login'

    def get(self, request):
        from src.models.webauthn import WebAuthnCredential
        return Response({**_status(request.auth),
                         'has_passkeys': WebAuthnCredential.objects.filter(user=request.user).exists()})


class UnlockView(SiteAdminAPIView):
    """
    POST ``{password, secret_key}``: the website's step-up
    (``admin_v2_step_up``: shared rate limit, log rows, constant-time secret).
    Failures are 403 with the website's message: ``admin_rate_limited``,
    ``admin_wrong_password``, ``admin_wrong_secret``, ``admin_not_configured``.
    """
    requires_unlock = False
    web_page = 'admin_v2_login'

    def post(self, request):
        from src.view.admin_v2 import admin_v2_step_up
        password = request.data.get('password', '') if hasattr(request.data, 'get') else ''
        secret = request.data.get('secret_key', '') if hasattr(request.data, 'get') else ''
        if not isinstance(password, str) or not isinstance(secret, str) or len(password) > 500 or len(secret) > 500:
            raise BadRequest('Enter your password and the secret key.', code='invalid_input')
        code, message = admin_v2_step_up(_http(request), request.user, password, secret, via=' from the app')
        if code != 'ok':
            raise exceptions.PermissionDenied(message, code=f'admin_{code}')
        unlock_device(request.auth)
        return Response(_status(request.auth))


class PasskeyUnlockBeginView(SiteAdminAPIView):
    """
    POST → ``{challenge_id, expires_at, options}`` (v4.4.3): the website's
    passkey step-up (``admin_v2_passkey_options``: the member's own keys,
    user verification required, the shared rate limit). The challenge is a
    ``MobileAuthTicket`` bound to this member and this install, single use,
    120 seconds. Refusals are 403 ``admin_rate_limited`` / ``admin_no_passkeys``
    with the website's message.
    """
    requires_unlock = False
    web_page = 'admin_v2_login'

    def post(self, request):
        from webauthn import options_to_json
        from src.mobile.views_auth import _b64url
        from src.models.mobile import MobileAuthTicket
        from src.view.admin_v2 import admin_v2_passkey_options
        from src.view.webauthn import rp_config

        rp_id, _ = rp_config(_http(request))
        code, message, options = admin_v2_passkey_options(request.user, rp_id)
        if code != 'ok':
            raise exceptions.PermissionDenied(message, code=f'admin_{code}')
        raw, ticket = MobileAuthTicket.issue(
            MobileAuthTicket.PURPOSE_ADMIN_PASSKEY, user=request.user, device=request.auth,
            payload={'challenge': _b64url(options.challenge)})
        return Response({
            'challenge_id': raw,
            'expires_at': ticket.expires_at.isoformat(),
            'options': json.loads(options_to_json(options)),
        })


class PasskeyUnlockCompleteView(SiteAdminAPIView):
    """
    POST ``{challenge_id, credential}`` → ``{unlocked, unlocked_until}``
    (v4.4.3). The challenge must be a live, unused one issued to this member
    on this install (taken atomically, so it can't be replayed); then the
    website's checks (``admin_v2_passkey_verify``) with user verification
    enforced and the app's origins (as the other app passkey flows). Success
    is the same 12-hour unlock of this install as the password step.
    Failures are 403 ``admin_<code>`` with the website's message; a bad
    challenge (``admin_bad_challenge``) counts toward the shared limit.
    """
    requires_unlock = False
    web_page = 'admin_v2_login'

    def post(self, request):
        from django.core.cache import cache
        from webauthn import base64url_to_bytes
        from src.mobile.views_auth import expected_passkey_origins
        from src.models.mobile import MobileAuthTicket
        from src.view import admin_v2
        from src.view.webauthn import rp_config

        user = request.user
        rate_key = f'admin_v2_attempts_{user.pk}'
        if cache.get(rate_key, 0) >= admin_v2.ADMIN_V2_MAX_ATTEMPTS:
            raise exceptions.PermissionDenied(
                'Too many failed attempts. Try again in 15 minutes.', code='admin_rate_limited')
        data = request.data if isinstance(request.data, dict) else {}
        challenge_id, credential = data.get('challenge_id'), data.get('credential')
        if not isinstance(challenge_id, str) or not isinstance(credential, (dict, str)) \
                or len(challenge_id) > 200:
            raise BadRequest('challenge_id and credential are required.', code='invalid_input')

        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_ADMIN_PASSKEY, challenge_id,
                                          device=request.auth)
        if ticket is None or ticket.user_id != user.pk:
            cache.set(rate_key, cache.get(rate_key, 0) + 1, admin_v2.ADMIN_V2_LOCKOUT_SECONDS)
            admin_v2._security_logger.warning(
                f'Admin-v2 passkey auth from the app: unknown, used or expired challenge from {user.username}')
            raise exceptions.PermissionDenied(
                'That passkey request expired. Try again.', code='admin_bad_challenge')

        rp_id, _ = rp_config(_http(request))
        code, message = admin_v2.admin_v2_passkey_verify(
            _http(request), user,
            credential if isinstance(credential, str) else json.dumps(credential),
            base64url_to_bytes(ticket.payload['challenge']),
            rp_id, expected_passkey_origins(request),
            require_user_verification=True, via=' from the app')
        if code != 'ok':
            raise exceptions.PermissionDenied(message, code=f'admin_{code}')
        unlock_device(request.auth)
        return Response(_status(request.auth))


class LockView(SiteAdminAPIView):
    """POST: end this install's unlock, with the website's `admin_v2_logout` row."""
    requires_unlock = False
    web_page = 'admin_v2_login'

    def post(self, request):
        was_unlocked = unlocked_until(request.auth) is not None
        lock_device(request.auth)
        if was_unlocked:
            ActivityLog.log_activity(
                action_type='admin_v2_logout', user=request.user,
                description=f'{request.user.get_display_name()} logged out of Admin v2 (app)',
                request=_http(request))
        return Response(_status(request.auth))


# ── Home ─────────────────────────────────────────────────────────────────────

def _flag(f):
    return {
        'id': f.pk, 'name': f.name, 'display_name': f.display_name, 'description': f.description,
        'category': f.category, 'category_label': f.get_category_display(), 'is_enabled': f.is_enabled,
        'last_toggled_by': f.last_toggled_by or None, 'last_toggled_at': _iso(f.last_toggled_at),
    }


def _page(p):
    return {
        'id': p.pk, 'url_name': p.url_name, 'display_name': p.display_name, 'description': p.description,
        'is_enabled': p.is_enabled, 'last_toggled_by': p.last_toggled_by or None,
        'last_toggled_at': _iso(p.last_toggled_at),
    }


def _lockdown(user, request):
    from src.models import SystemLockdown
    from src.utils.security_utils import get_client_ip
    lockdown = SystemLockdown.get_instance()
    return {
        'active': lockdown.is_active,
        'reason': lockdown.reason if lockdown.is_active else '',
        'activated_at': _iso(lockdown.activated_at) if lockdown.is_active else None,
        'activated_by': _person(lockdown.activated_by) if lockdown.is_active else None,
        'whitelisted_ips': list(lockdown.whitelisted_ips or []),
        # The website's lockdown page is for is_admin members only.
        'can_manage': bool(user.is_admin),
        # The address this request came from, for "keep this phone allowed"
        # (the same helper the website's suggestion uses).
        'current_ip': get_client_ip(_http(request)) or None,
    }


class HomeView(SiteAdminAPIView):
    web_page = 'admin_v2_dashboard'

    def get(self, request):
        from django.core.cache import cache
        from src.admin_v2_attention import attention_items
        from src.models import IPBlacklist, LoginLockout, QuarantinedAccount
        from src.models_feature_flags import FeatureFlag, PageToggle
        from src.view.admin_v2 import dashboard_celery_summary, seed_dashboard_defaults

        now = timezone.now()
        day_ago = now - timezone.timedelta(hours=24)
        users = ParliamentUser.objects.aggregate(
            total=Count('user_id', filter=~Q(member_status='Removed')),
            active=Count('user_id', filter=Q(member_status='Active')),
            pledges=Count('user_id', filter=Q(member_type='Pledge')),
            last_24h=Count('user_id', filter=Q(last_login__gte=day_ago)),
            never_logged_in=Count('user_id', filter=Q(last_login__isnull=True)),
        )
        alerts = LoginAlert.objects.aggregate(
            new=Count('pk', filter=Q(status='new')),
            critical=Count('pk', filter=Q(status='new', severity='critical')),
        )
        security = {
            'new_alerts': alerts['new'],
            'critical_alerts': alerts['critical'],
            'active_lockouts': LoginLockout.objects.filter(is_cleared=False, expires_at__gt=now).count(),
            'quarantined': QuarantinedAccount.objects.filter(released_at__isnull=True).filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=now)).count(),
            'blocked_ips': IPBlacklist.objects.filter(is_active=True).count(),
        }

        seed_dashboard_defaults()   # the same rows the dashboard and Maintenance page create
        flags = list(FeatureFlag.objects.order_by('category', 'display_name'))
        maintenance = next((f for f in flags if f.name == 'maintenance_mode'), None)
        return Response({
            **_status(request.auth),
            'stats': {'members': users, 'security': security},
            'celery': dashboard_celery_summary(),
            'attention': attention_items(),
            'maintenance': {
                'id': maintenance.pk, 'is_enabled': maintenance.is_enabled,
                'blocked': cache.get('maintenance_blocked_count', 0),
            } if maintenance else None,
            'lockdown': _lockdown(request.user, request),
            'flags': [_flag(f) for f in flags if f.name != 'maintenance_mode'],
            'pages': [_page(p) for p in PageToggle.objects.order_by('display_name')],
        })


def _wanted(request):
    value = request.data.get('enabled') if hasattr(request.data, 'get') else None
    if not isinstance(value, bool):
        raise BadRequest('Say whether it should be on or off.', code='invalid_input')
    return value


class FlagView(SiteAdminAPIView):
    """
    POST ``{enabled}``: set a feature flag (maintenance mode included) through
    `apply_feature_flag_toggle`. Already in that state → nothing changes and
    nothing is logged (a stale screen can't flip it the wrong way).
    """
    web_page = 'admin_v2_dashboard'

    def post(self, request, flag_id):
        from src.models_feature_flags import FeatureFlag
        from src.view.admin_v2 import apply_feature_flag_toggle
        wanted = _wanted(request)
        flag = get_object_or_404(FeatureFlag, pk=flag_id)
        changed = flag.is_enabled != wanted
        if changed:
            apply_feature_flag_toggle(_http(request), request.user, flag)
        return Response({'flag': _flag(flag), 'changed': changed})


class PageView(SiteAdminAPIView):
    """POST ``{enabled}``: set a page switch through `apply_page_toggle`."""
    web_page = 'admin_v2_dashboard'

    def post(self, request, toggle_id):
        from src.models_feature_flags import PageToggle
        from src.view.admin_v2 import apply_page_toggle
        wanted = _wanted(request)
        toggle = get_object_or_404(PageToggle, pk=toggle_id)
        changed = toggle.is_enabled != wanted
        if changed:
            apply_page_toggle(_http(request), request.user, toggle)
        return Response({'page': _page(toggle), 'changed': changed})


class LockdownView(SiteAdminAPIView):
    """
    POST ``{action: activate, reason, whitelisted_ips: [..]}`` or
    ``{action: deactivate}``, through the website's helpers (log line and
    security alert). is_admin only, as the website's lockdown page.
    """
    web_page = 'admin_v2_lockdown'
    needs_is_admin = True

    def post(self, request):
        from src.view.admin_v2 import activate_lockdown, deactivate_lockdown
        action = _text(request.data, 'action', 20)
        if action == 'activate':
            reason = _text(request.data, 'reason', 1000) or 'Emergency lockdown activated'
            raw = request.data.get('whitelisted_ips', []) if hasattr(request.data, 'get') else []
            if isinstance(raw, str):
                raw = raw.split(',')
            if not isinstance(raw, list):
                raise BadRequest('Allowed addresses must be a list.', code='invalid_input')
            ips = [ip.strip() for ip in raw if isinstance(ip, str) and ip.strip()][:50]
            activate_lockdown(request.user, reason, ips)
        elif action == 'deactivate':
            deactivate_lockdown(request.user)
        else:
            raise BadRequest('Unknown action.', code='invalid_input')
        return Response({'lockdown': _lockdown(request.user, request)})


# ── Members ──────────────────────────────────────────────────────────────────

def _member_row(u):
    from src.mobile.views_directory import photo_path
    return {
        'user_id': str(u.user_id), 'name': u.get_display_name(), 'username': u.username,
        'member_type': u.member_type, 'member_status': u.member_status, 'is_admin': bool(u.is_admin),
        'photo': photo_path(u),
    }


class MemberSearchView(SiteAdminAPIView):
    """GET ``?q=&status=&type=&page=``: the Users page's search (`users_queryset`)."""
    web_page = 'admin_v2_manage_users'

    def get(self, request):
        from src.view.admin_v2 import users_queryset
        q = (request.query_params.get('q') or '').strip()[:80]
        status = (request.query_params.get('status') or '').strip()[:20]
        mtype = (request.query_params.get('type') or '').strip()[:20]
        page = Paginator(users_queryset(status, mtype, '', '', q), MEMBER_PAGE_SIZE).get_page(
            request.query_params.get('page'))
        return Response({
            'members': [_member_row(u) for u in page.object_list],
            'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
            'has_next': page.has_next(),
        })


def _two_factor_requirement(member):
    from src.models import TwoFactorRequirement
    from src.models_feature_flags import SiteSetting
    req = TwoFactorRequirement.objects.filter(user=member).select_related('set_by').first()
    return {
        'requirement': req.requirement if req else 'policy',
        'reason': req.reason if req else '',
        'set_by': _person(req.set_by) if req else None,
        'policy': SiteSetting.get_setting('2fa_policy_mode', 'none'),
    }


def _device(d):
    return {
        'public_id': str(d.public_id), 'platform': d.platform, 'platform_label': d.get_platform_display(),
        'device_name': d.device_name, 'app_version': d.app_version,
        'verified_via': d.get_verified_via_display(), 'has_push': bool(d.push_token),
        'created_at': _iso(d.created_at), 'last_seen_at': _iso(d.last_seen_at),
        'revoked_at': _iso(d.revoked_at), 'revoked_reason': d.revoked_reason,
    }


def _login(entry):
    return {
        'id': entry.pk, 'timestamp': _iso(entry.timestamp), 'status': entry.status,
        'status_label': entry.get_status_display(), 'location': entry.location_display,
        'ip_address': entry.ip_address or None, 'device': ' · '.join(
            b for b in (entry.device_type, entry.browser, entry.os) if b),
        'is_suspicious': bool(entry.is_suspicious), 'risk_level': entry.risk_level,
    }


def _alert_notes(alert):
    # A forced password reset keeps the new password in its note (the website
    # shows it there); it never goes to a phone.
    notes = alert.resolution_notes or ''
    return None if notes.startswith('New password:') else (notes or None)


def _alert(a, with_member=True):
    row = {
        'id': a.pk, 'title': a.title, 'description': a.description, 'alert_type': a.alert_type,
        'alert_type_label': a.get_alert_type_display(), 'severity': a.severity,
        'status': a.status, 'status_label': a.get_status_display(), 'created_at': _iso(a.created_at),
        'reviewed_by': _person(a.reviewed_by), 'reviewed_at': _iso(a.reviewed_at), 'notes': _alert_notes(a),
    }
    if with_member:
        row['member'] = _person(a.user)
    return row


def _lockout(lo):
    return {
        'id': lo.pk, 'ip_address': lo.ip_address, 'username': lo.username, 'source': lo.source,
        'source_label': lo.get_source_display(), 'locked_at': _iso(lo.locked_at), 'expires_at': _iso(lo.expires_at),
        'is_cleared': lo.is_cleared, 'cleared_at': _iso(lo.cleared_at), 'cleared_by': _person(lo.cleared_by),
    }


def _member_payload(member, request):
    from src.view.admin_v2_member import member_page_data
    data = member_page_data(member, request.user)
    chapter = data['chapter']
    service = chapter['service']
    current = request.auth.pk
    return {
        'member': {
            **_member_row(member),
            'email': member.email or None, 'phone_number': getattr(member, 'phone_number', '') or None,
            'pledge_class': member.pledge_class or None, 'is_quarantined': bool(member.is_quarantined),
            'last_login': _iso(member.last_login), 'last_seen_at': _iso(getattr(member, 'last_seen_at', None)),
            'is_self': member.pk == request.user.pk,
        },
        'roles': [r.name for r in data['roles']],
        'committees': [{'name': c.name, 'chair': c in data['chaired']} for c in data['committees']],
        'security': {
            'has_totp': data['has_totp'],
            'passkeys': [{'name': p.name, 'created_at': _iso(p.created_at), 'last_used_at': _iso(p.last_used_at)}
                         for p in data['passkeys']],
            'two_factor': _two_factor_requirement(member),
            'failed_30d': data['failed_30d'],
            'lockouts': [_lockout(lo) for lo in data['lockouts']],
            'watch': {'reason': data['watch'].reason} if data['watch'] else None,
        },
        'devices': [{**_device(d), 'current': d.pk == current} for d in data['devices']],
        'active_devices': data['active_devices'],
        'sessions': [{
            'id': s.pk, 'device_type': s.device_type, 'browser': s.browser, 'operating_system': s.operating_system,
            'location': s.location, 'created_at': _iso(s.created_at), 'last_activity': _iso(s.last_activity),
        } for s in data['sessions']],
        'logins': [_login(entry) for entry in data['logins']],
        'activity': [{
            'id': a.pk, 'timestamp': _iso(a.timestamp), 'type_label': a.get_action_type_display(),
            'description': a.description,
        } for a in data['activity']],
        'chapter': {
            'attendance': chapter['attendance'],
            'excuses_pending': chapter['excuses_pending'],
            'service': {
                'period': chapter['service_period'].name if chapter['service_period'] else None,
                'approved_hours': float(service['approved_hours']),
                'expected_hours': float(service['expected_hours']),
                'pending_hours': float(service['pending_hours']),
                'progress_percent': service['progress_percent'],
            } if service else None,
        },
    }


class MemberView(SiteAdminAPIView):
    """GET: the member page (`member_page_data`)."""
    web_page = 'admin_v2_member'

    def get(self, request, user_id):
        return Response(_member_payload(_member(user_id), request))


class MemberSecurityView(SiteAdminAPIView):
    """GET: the member's Login security page (`login_security_data`), read only."""
    web_page = 'admin_v2_user_login_security'

    def get(self, request, user_id):
        from src.view.admin_v2 import login_security_data
        member = _member(user_id)
        logins, alerts, ip_info, stats = login_security_data(member)
        return Response({
            'member': _member_row(member),
            'stats': stats,
            'logins': [_login(entry) for entry in logins],
            'alerts': [_alert(a, with_member=False) for a in alerts],
            'ips': [{**ip, 'last_used': _iso(ip['last_used'])} for ip in ip_info],
        })


class MemberSignOutView(SiteAdminAPIView):
    """
    POST: every web session and app install of the member signed out
    (`sign_member_out_everywhere`). On yourself, this phone stays signed in,
    as the website keeps the browser you did it from.
    """
    web_page = 'admin_v2_member'

    def post(self, request, user_id):
        from src.view.admin_v2_member import sign_member_out_everywhere
        member = _member(user_id)
        keep = request.auth.pk if member.pk == request.user.pk else None
        sessions = sign_member_out_everywhere(_http(request), request.user, member, keep_device_id=keep)
        return Response({'ok': True, 'sessions': sessions})


class MemberDeviceRevokeView(SiteAdminAPIView):
    """POST: sign out one app install of the member (`revoke_member_device`)."""
    web_page = 'admin_v2_member'

    def post(self, request, user_id, device_id):
        from src.models.mobile import MobileDevice
        from src.view.admin_v2_member import revoke_member_device
        member = _member(user_id)
        try:
            device_uuid = uuid.UUID(str(device_id))
        except ValueError:
            raise Http404
        device = get_object_or_404(MobileDevice, public_id=device_uuid, user=member)
        revoked = revoke_member_device(_http(request), request.user, member, device)
        return Response({'ok': True, 'revoked': revoked, 'current': device.pk == request.auth.pk})


class MemberReleaseLockoutView(SiteAdminAPIView):
    """POST: release every login lockout on the member (`release_member_lockouts`)."""
    web_page = 'admin_v2_release_user_lockout'

    def post(self, request, user_id):
        from src.view.admin_v2 import release_member_lockouts
        member = _member(user_id)
        cleared = release_member_lockouts(_http(request), request.user, member)
        return Response({'ok': True, 'lockout_rows': cleared['lockout_rows']})


class MemberPasswordResetView(SiteAdminAPIView):
    """
    POST ``{reason, password_type: random|custom, custom_password, send_email}``
    through `perform_force_password_reset`. The new password never comes back:
    a random one must be emailed (so the member needs an address on file), a
    custom one is the one the admin typed. Anything else is the website's job.
    """
    web_page = 'admin_v2_force_password_reset'

    def post(self, request, user_id):
        from src.view.admin_v2 import perform_force_password_reset
        member = _member(user_id)
        if member.pk == request.user.pk:
            raise BadRequest('Change your own password in Settings.', code='invalid_input')
        data = request.data if hasattr(request.data, 'get') else {}
        password_type = 'custom' if data.get('password_type') == 'custom' else 'random'
        send_email = data.get('send_email') is True
        custom = data.get('custom_password', '')
        if not isinstance(custom, str) or len(custom) > 200:
            raise BadRequest('That password is too long.', code='invalid_input')
        if password_type == 'random' and not (send_email and member.email):
            raise BadRequest(
                'A random password has to be emailed to the member from the app. '
                + ('Turn on "Email it".' if member.email else
                   'They have no email on file: set a password yourself, or use the website.'),
                code='invalid_input')
        result = perform_force_password_reset(
            _http(request), request.user, member,
            reason=_text(data, 'reason', 500) or 'Security concern flagged by admin',
            password_type=password_type, custom_password=custom, send_email=send_email)
        if result['error']:
            raise BadRequest(result['error'], code='invalid_input')
        return Response({'ok': True, 'email_sent': result['email_sent'],
                         'email_failed': bool(result['email_error'])})


class MemberResetTwoFactorView(SiteAdminAPIView):
    """POST: remove the member's authenticator (`reset_member_2fa`)."""
    web_page = 'admin_v2_two_factor_reset'

    def post(self, request, user_id):
        from src.view.admin_two_factor import reset_member_2fa
        member = _member(user_id)
        removed = reset_member_2fa(_http(request), request.user, member)
        return Response({'ok': True, 'removed': removed})


class MemberTwoFactorRequirementView(SiteAdminAPIView):
    """POST ``{requirement: required|exempt|clear, reason}`` (`set_member_2fa_requirement`)."""
    web_page = 'admin_v2_two_factor_set_requirement'

    def post(self, request, user_id):
        from src.view.admin_two_factor import set_member_2fa_requirement
        member = _member(user_id)
        requirement = _text(request.data, 'requirement', 20)
        if not set_member_2fa_requirement(_http(request), request.user, member, requirement,
                                          _text(request.data, 'reason', 500)):
            raise BadRequest('Invalid requirement', code='invalid_input')
        return Response({'ok': True, 'two_factor': _two_factor_requirement(member)})


# ── Security ─────────────────────────────────────────────────────────────────

class LockoutsView(SiteAdminAPIView):
    """GET: active lockouts, recently expired and recently cleared (the Lockouts page)."""
    web_page = 'admin_v2_lockouts'
    needs_is_admin = True

    def get(self, request):
        from src.models import LoginLockout
        now = timezone.now()
        base = LoginLockout.objects.select_related('cleared_by')
        active = base.filter(is_cleared=False, expires_at__gt=now).order_by('-locked_at')
        return Response({
            'active': [_lockout(lo) for lo in active[:LIST_LIMIT]],
            'active_count': active.count(),
            'expired': [_lockout(lo) for lo in base.filter(is_cleared=False, expires_at__lte=now)
                        .order_by('-locked_at')[:20]],
            'cleared': [_lockout(lo) for lo in base.filter(is_cleared=True).order_by('-cleared_at')[:20]],
        })


class LockoutClearView(SiteAdminAPIView):
    """POST: clear one lockout row and its cache keys (`clear_lockout_row`)."""
    web_page = 'admin_v2_lockouts'
    needs_is_admin = True

    def post(self, request, lockout_id):
        from src.models import LoginLockout
        from src.view.admin_v2 import clear_lockout_row
        lockout = get_object_or_404(LoginLockout, pk=lockout_id)
        cleared = clear_lockout_row(request.user, lockout)
        return Response({'ok': True, 'lockout_rows': cleared['lockout_rows']})


class AlertsView(SiteAdminAPIView):
    """GET ``?status=&severity=``: security alerts, newest 100 (the Alerts page)."""
    web_page = 'admin_v2_security_alerts'

    def get(self, request):
        status = (request.query_params.get('status') or '').strip()[:20]
        severity = (request.query_params.get('severity') or '').strip()[:20]
        qs = LoginAlert.objects.select_related('user', 'reviewed_by').order_by('-created_at')
        if status:
            qs = qs.filter(status=status)
        if severity:
            qs = qs.filter(severity=severity)
        counts = LoginAlert.objects.aggregate(
            total=Count('pk'), new=Count('pk', filter=Q(status='new')),
            investigating=Count('pk', filter=Q(status='investigating')),
            critical=Count('pk', filter=Q(status='new', severity='critical')),
            high=Count('pk', filter=Q(status='new', severity='high')),
        )
        return Response({'stats': counts, 'alerts': [_alert(a) for a in qs[:LIST_LIMIT]]})


class AlertDismissView(SiteAdminAPIView):
    """POST ``{notes}``: resolve one alert (`dismiss_login_alert`)."""
    web_page = 'admin_v2_dismiss_alert'

    def post(self, request, alert_id):
        from src.view.admin_v2 import dismiss_login_alert
        alert = get_object_or_404(LoginAlert, pk=alert_id)
        dismiss_login_alert(request.user, alert, _text(request.data, 'notes', 1000) or 'Dismissed by admin')
        return Response({'ok': True, 'new_count': LoginAlert.objects.filter(status='new').count()})


class AlertDismissAllView(SiteAdminAPIView):
    """POST: resolve every new alert (`dismiss_all_login_alerts`)."""
    web_page = 'admin_v2_dismiss_all_alerts'

    def post(self, request):
        from src.view.admin_v2 import dismiss_all_login_alerts
        return Response({'ok': True, 'dismissed': dismiss_all_login_alerts(_http(request), request.user)})


class AuditLogView(SiteAdminAPIView):
    """GET ``?action=&actor=&page=``: the audit log, read only (`audit_log_queryset`)."""
    web_page = 'admin_v2_audit_log'
    needs_is_admin = True

    def get(self, request):
        from src.models import AdminActionLog
        from src.view.admin_v2 import audit_log_queryset
        action = (request.query_params.get('action') or '').strip()[:30]
        actor = (request.query_params.get('actor') or '').strip()[:40]
        page = Paginator(audit_log_queryset(action, actor), AUDIT_PAGE_SIZE).get_page(
            request.query_params.get('page'))
        body = {
            'entries': [{
                'id': e.pk, 'timestamp': _iso(e.timestamp), 'actor': _person(e.actor),
                'action': e.action, 'action_label': e.get_action_display(),
                'target': _person(e.target_user), 'target_repr': e.target_repr, 'detail': e.detail,
                'ip_address': e.ip_address,
            } for e in page.object_list],
            'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
            'has_next': page.has_next(),
        }
        if page.number == 1:
            body['actions'] = [{'code': c, 'label': label} for c, label in AdminActionLog.ACTION_CHOICES]
        return Response(body)


# ── App installs ─────────────────────────────────────────────────────────────

class AppInstallsView(SiteAdminAPIView):
    """GET: the App installs page (`mobile_overview`); setup checks are booleans only."""
    web_page = 'admin_v2_mobile'

    def get(self, request):
        from src.view.admin_v2_mobile import mobile_overview
        data = mobile_overview()
        return Response({
            'stats': data['stats'],
            'versions': [{'platform': v['platform'], 'app_version': v['app_version'], 'count': v['n']}
                         for v in data['versions']],
            'recent': [{**_device(d), 'member': _person(d.user)} for d in data['recent']],
            'push_members': [_person(m) for m in data['push_members'][:200]],
            'awaiting_receipts': data['awaiting_receipts'],
            'setup': [{'label': label, 'ok': ok} for label, ok, _hint in data['setup']],
        })


class TestPushView(SiteAdminAPIView):
    """POST ``{user_id}``: the website's test push (`send_test_push`)."""
    web_page = 'admin_v2_mobile_push'

    def post(self, request):
        from src.view.admin_v2_mobile import send_test_push
        member = _member(_text(request.data, 'user_id', 40))
        outcome, accepted, devices = send_test_push(_http(request), request.user, member)
        return Response({'outcome': outcome, 'accepted': accepted, 'devices': devices})
