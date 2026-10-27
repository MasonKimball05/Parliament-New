"""
Mobile sign-in endpoints (``/api/mobile/v1/auth/...``) and ``me/``.

Flows
-----
Password::

    POST auth/login/  {identifier, password, platform, device_name, app_version}
      → 200 tokens                                (no 2FA device, none required)
      → 200 {mfa_required: true, ticket}          (has a TOTP / backup device)
      → 403 {error: 'mfa_setup_required'}         (policy requires 2FA, none set up:
                                                   set it up on the web first)
    POST auth/verify/ {ticket, code}
      → 200 tokens
      → 401 {error: 'invalid_code', ticket, attempts_remaining}   (retry)
      → 401 {error: 'invalid_code', attempts_remaining: 0}        (start over)
      → 423 {error: 'locked', retry_after}   (IP locked; the ticket is NOT used up)

Passkey::

    POST auth/passkey/begin/    → {challenge_id, options}
    POST auth/passkey/complete/ {challenge_id, credential, platform, ...} → tokens

Tokens::

    POST auth/refresh/ {refresh_token} → tokens (rotated) | 401 {error: reason}
    POST auth/logout/  (Bearer)        → revokes this device

The password checks are ``src/login_pipeline.attempt_password_login`` — the
same function the web login page runs, so lockouts, the blacklist, quarantine
and the alert emails are shared. Nothing here calls ``login()`` or writes to a
session. ``user_logged_in`` is sent by hand once a device is issued (LoginHistory,
LoginAlert, watch-flag, ActivityLog), i.e. once per COMPLETE sign-in — after the
second factor, not after the password as on the web.

The 2FA ticket and retries
--------------------------
A ticket is single-use (``MobileAuthTicket.consume``), so a wrong code burns it.
To let the member retry a typo, the failure response carries a FRESH ticket for
the same user, with the original expiry (retrying never extends the 5-minute
window) and the attempt count carried in its payload. After
``MFA_TICKET_MAX_ATTEMPTS`` wrong codes no new ticket is issued and the app must
start again from the password, which is itself under the login lockouts. Every
wrong code also feeds the web's per-user 2FA failure counter
(``_record_2fa_failure``: the critical alert after 5 in 15 minutes), and
django-otp's own per-device throttle still applies to TOTP.
"""
import base64
import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.core.cache import cache
from django.utils import timezone
from django_otp import user_has_device
from rest_framework.response import Response

from src import login_pipeline as lp
from src.mobile.api import MobileAPIView, PreAuthAPIView
from src.mobile.tokens import create_device, rotate, token_response
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.utils.security_utils import get_client_ip, run_post_auth_pipeline

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')

MFA_TICKET_MAX_ATTEMPTS = 5

#: Shown for every refusal that must not say WHY (quarantine, blacklist, a
#: Removed member): the same text whatever the reason.
ACCESS_DENIED_TEXT = ('Sign-in is not available right now. '
                      'Contact an administrator if this keeps happening.')

# Passkey failures use the same IP bucket the web passkey endpoint uses
# (LoginRateLimitMiddleware: 10 failures / 15 min → 30 min lockout).
_PASSKEY_MAX_FAILURES = 10
_PASSKEY_WINDOW = 15 * 60
_PASSKEY_LOCKOUT = 30 * 60

_PLATFORMS = dict(MobileDevice.PLATFORM_CHOICES)


# ── helpers ──────────────────────────────────────────────────────────────────

def _err(error, status, **extra):
    return Response({'error': error, **extra}, status=status)


def _data(request):
    data = request.data
    return data if isinstance(data, dict) else None


def _str(data, key, max_len, required=True):
    """A stripped string field, '' when absent and optional; None when invalid."""
    value = data.get(key)
    if value is None or value == '':
        return None if required else ''
    if not isinstance(value, str) or len(value) > max_len:
        return None
    return value.strip() if key != 'password' else value


def _device_fields(data):
    """(platform, device_name, app_version) or None if invalid."""
    platform = _str(data, 'platform', 10)
    if platform not in _PLATFORMS:
        return None
    device_name = _str(data, 'device_name', 100, required=False)
    app_version = _str(data, 'app_version', 30, required=False)
    if device_name is None or app_version is None:
        return None
    return platform, device_name, app_version


def _ip(request):
    return get_client_ip(request._request)


def _ua(request):
    return request.META.get('HTTP_USER_AGENT', 'unknown')[:200]


def _retry_after(until):
    return max(1, int((until - timezone.now()).total_seconds()))


def _account_unusable(user):
    """A reason the account can't hold a device, or ''. Generic to the caller."""
    if not user.is_active:
        return 'inactive'
    if getattr(user, 'is_quarantined', False):
        return 'quarantined'
    if getattr(user, 'member_status', None) == 'Removed':
        return 'removed'
    return ''


def _middleware_lockout(ip_address, username=None):
    """
    The web's OTHER lockout (LoginRateLimitMiddleware's keys) only watches
    /login/, so honour its locks here too — a lock either system set closes
    both doors. Returns seconds remaining-ish or None.
    """
    if cache.get(f'login_lockout_ip_{ip_address}'):
        return _PASSKEY_LOCKOUT
    if username and cache.get(f'login_lockout_user_{username.strip().lower()}'):
        return _PASSKEY_LOCKOUT
    return None


def _issue_device(request, user, via, platform, device_name, app_version, extra=None):
    """The end of every successful sign-in: a device, its tokens, the signal."""
    # run_post_auth_pipeline (use_session=False) left its geo verdict here; the
    # device keeps it so /m/handoff/ can give the web view the same
    # login_geo_* session keys a website sign-in gets (non-US export block).
    pipeline = getattr(request._request, '_login_pipeline', None) or {}
    device, access, refresh = create_device(
        user, via, platform, device_name=device_name, app_version=app_version,
        login_geo=pipeline.get('geo') or {},
        login_geo_suspicious=bool(pipeline.get('is_foreign')))
    # LoginHistory / LoginAlert / watch-flag / ActivityLog — the receivers read
    # request.META and the `_login_pipeline` stash, never the session.
    user_logged_in.send(sender=user.__class__, request=request._request, user=user)
    security_logger.info(
        f'Mobile sign-in [{via}]: {user.username} on {platform} '
        f'(device {device.public_id}) from {_ip(request)}')
    body = {'mfa_required': False, **token_response(device, access, refresh)}
    if extra:
        body.update(extra)
    return Response(body, status=200)


def _post_auth(request, user, method):
    """run_post_auth_pipeline without a session. Returns an error Response or None."""
    error_response, _ = run_post_auth_pipeline(
        request._request, user, _ip(request), _ua(request), method=method, use_session=False)
    if error_response is not None:
        return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)
    return None


# ── password ─────────────────────────────────────────────────────────────────

class LoginView(PreAuthAPIView):
    throttle_scope = 'login'

    def post(self, request):
        data = _data(request)
        if data is None:
            return _err('invalid_request', 400)
        identifier = _str(data, 'identifier', 254)
        password = _str(data, 'password', 4096)
        fields = _device_fields(data)
        if not identifier or not password or fields is None:
            return _err('invalid_request', 400)
        platform, device_name, app_version = fields
        ip_address = _ip(request)

        if _middleware_lockout(ip_address, identifier):
            return _err('locked', 423, retry_after=_PASSKEY_LOCKOUT)

        result = lp.attempt_password_login(
            request._request, identifier, password,
            ip_address=ip_address, user_agent=_ua(request),
            method='mobile_password', use_session=False,
        )

        if result.outcome in (lp.IP_LOCKED, lp.ACCOUNT_LOCKED):
            return _err('locked', 423, retry_after=_retry_after(result.lockout_until))
        if result.outcome in (lp.INVALID, lp.DISABLED):
            if result.now_locked and result.lockout_until:
                return _err('locked', 423, retry_after=_retry_after(result.lockout_until))
            return _err('invalid_credentials', 401)
        if result.outcome == lp.MISSING_CREDENTIALS:
            return _err('invalid_request', 400)
        if result.outcome in (lp.BLACKLISTED, lp.QUARANTINED, lp.BLOCKED):
            return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)
        if result.outcome != lp.OK:   # pragma: no cover — new outcome, fail closed
            return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)

        user = result.user
        if _account_unusable(user):
            return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)

        if user_has_device(user):
            raw, ticket = MobileAuthTicket.issue(
                MobileAuthTicket.PURPOSE_MFA, user=user, payload={
                    'platform': platform, 'device_name': device_name,
                    'app_version': app_version, 'attempts': 0,
                })
            return Response({
                'mfa_required': True,
                'ticket': raw,
                'ticket_expires_at': ticket.expires_at.isoformat(),
            }, status=200)

        from src.middleware.two_factor import Enforce2FAMiddleware
        if Enforce2FAMiddleware.user_requires_2fa(user):
            return _err('mfa_setup_required', 403, mfa_setup_required=True,
                        detail='Set up two-factor authentication on the website first.')

        return _issue_device(request, user, 'password', platform, device_name, app_version)


class VerifyView(PreAuthAPIView):
    throttle_scope = 'verify'

    def post(self, request):
        from src.view.two_factor import _record_2fa_failure, check_second_factor

        data = _data(request)
        if data is None:
            return _err('invalid_request', 400)
        raw_ticket = _str(data, 'ticket', 200)
        code = _str(data, 'code', 32)
        if not raw_ticket or not code:
            return _err('invalid_request', 400)
        code = code.strip().upper()

        # The IP lockout needs nothing from the ticket, so it is checked BEFORE
        # the ticket is consumed: a refusal here leaves the ticket usable (until
        # its own expiry) instead of burning it with no replacement.
        ip_address = _ip(request)
        locked, until = lp.is_rate_limited(ip_address)
        if locked:
            return _err('locked', 423, retry_after=_retry_after(until))

        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_MFA, raw_ticket)
        if ticket is None or ticket.user_id is None:
            return _err('invalid_ticket', 401)

        user = get_user_model().objects.filter(pk=ticket.user_id).first()
        if user is None or _account_unusable(user):
            return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)

        payload = dict(ticket.payload or {})
        device, kind, remaining = check_second_factor(user, code)
        if device is None:
            _record_2fa_failure(request._request, user=user)
            attempts = int(payload.get('attempts', 0)) + 1
            left = MFA_TICKET_MAX_ATTEMPTS - attempts
            if left <= 0:
                return _err('invalid_code', 401, attempts_remaining=0)
            payload['attempts'] = attempts
            new_raw, new_ticket = MobileAuthTicket.issue(
                MobileAuthTicket.PURPOSE_MFA, user=user, payload=payload)
            # Retrying never extends the original window.
            MobileAuthTicket.objects.filter(pk=new_ticket.pk).update(expires_at=ticket.expires_at)
            return _err('invalid_code', 401, ticket=new_raw, attempts_remaining=left,
                        ticket_expires_at=ticket.expires_at.isoformat())

        cache.delete(f'2fa_failures_{user.pk}')
        via = 'password_totp' if kind == 'totp' else 'password_backup'
        denied = _post_auth(request, user, method=f'mobile_{via}')
        if denied:
            return denied
        extra = {'backup_codes_remaining': remaining} if kind == 'backup' else None
        return _issue_device(
            request, user, via,
            payload.get('platform', 'ios'), payload.get('device_name', ''),
            payload.get('app_version', ''), extra=extra)


# ── passkey ──────────────────────────────────────────────────────────────────

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def expected_passkey_origins(request):
    """The site origin (as the web computes it) plus the Android APK origins."""
    from src.view.webauthn import rp_config
    _, origin = rp_config(request._request)
    return [origin] + list(getattr(settings, 'MOBILE_ANDROID_APK_KEY_HASH_ORIGINS', []))


def _passkey_ip_locked(ip_address):
    if lp.is_ip_whitelisted(ip_address):
        return None
    locked, until = lp.is_rate_limited(ip_address)
    if locked:
        return _retry_after(until)
    if cache.get(f'login_lockout_ip_{ip_address}'):
        return _PASSKEY_LOCKOUT
    return None


def _passkey_failed(ip_address):
    """Count a failed passkey sign-in in the web passkey's IP bucket."""
    if lp.is_ip_whitelisted(ip_address):
        return
    key = f'login_attempts_ip_{ip_address}'
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, _PASSKEY_WINDOW)
    if attempts >= _PASSKEY_MAX_FAILURES:
        cache.set(f'login_lockout_ip_{ip_address}', True, _PASSKEY_LOCKOUT)
        logging.getLogger('admin_actions').warning(
            f'Mobile passkey sign-in: IP {ip_address} locked out after {attempts} failures')


class PasskeyBeginView(PreAuthAPIView):
    throttle_scope = 'passkey'

    def post(self, request):
        import webauthn
        from webauthn import options_to_json
        from webauthn.helpers.structs import UserVerificationRequirement
        from src.view.webauthn import rp_config

        retry = _passkey_ip_locked(_ip(request))
        if retry:
            return _err('locked', 423, retry_after=retry)

        rp_id, _ = rp_config(request._request)
        options = webauthn.generate_authentication_options(
            rp_id=rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        raw, ticket = MobileAuthTicket.issue(
            MobileAuthTicket.PURPOSE_PASSKEY_LOGIN,
            payload={'challenge': _b64url(options.challenge)})
        return Response({
            'challenge_id': raw,
            'expires_at': ticket.expires_at.isoformat(),
            'options': json.loads(options_to_json(options)),
        }, status=200)


class PasskeyCompleteView(PreAuthAPIView):
    throttle_scope = 'passkey'

    def post(self, request):
        import webauthn
        from webauthn import base64url_to_bytes
        from src.models.users import member_defer
        from src.models.webauthn import WebAuthnCredential
        from src.view.webauthn import rp_config

        data = _data(request)
        if data is None:
            return _err('invalid_request', 400)
        challenge_id = _str(data, 'challenge_id', 200)
        credential = data.get('credential')
        fields = _device_fields(data)
        if not challenge_id or not isinstance(credential, (dict, str)) or fields is None:
            return _err('invalid_request', 400)
        platform, device_name, app_version = fields

        ip_address = _ip(request)
        retry = _passkey_ip_locked(ip_address)
        if retry:
            return _err('locked', 423, retry_after=retry)

        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_PASSKEY_LOGIN, challenge_id)
        if ticket is None:
            _passkey_failed(ip_address)
            return _err('invalid_challenge', 401)

        try:
            cred = json.loads(credential) if isinstance(credential, str) else credential
            if not isinstance(cred, dict):
                raise ValueError('credential is not an object')
            cred_id_bytes = base64url_to_bytes(cred['rawId'])
            credential_json = json.dumps(cred)
        except Exception:
            _passkey_failed(ip_address)
            return _err('invalid_request', 400)

        db_cred = (WebAuthnCredential.objects.select_related('user')
                   .defer(*member_defer('user')).filter(credential_id=cred_id_bytes).first())
        if db_cred is None:
            security_logger.warning(f'Mobile passkey sign-in: unknown credential from {ip_address}')
            _passkey_failed(ip_address)
            return _err('passkey_rejected', 401)

        rp_id, _ = rp_config(request._request)
        try:
            verification = webauthn.verify_authentication_response(
                credential=credential_json,
                expected_challenge=base64url_to_bytes(ticket.payload['challenge']),
                expected_rp_id=rp_id,
                expected_origin=expected_passkey_origins(request),
                credential_public_key=bytes(db_cred.public_key),
                credential_current_sign_count=db_cred.sign_count,
                require_user_verification=True,
            )
        except Exception as exc:
            security_logger.warning(
                f'Mobile passkey sign-in failed for credential owner {db_cred.user.username}: {exc}')
            _passkey_failed(ip_address)
            return _err('passkey_rejected', 401)

        db_cred.mark_used(verification.new_sign_count)

        user = db_cred.user
        if _account_unusable(user):
            return _err('access_denied', 403, detail=ACCESS_DENIED_TEXT)
        denied = _post_auth(request, user, method='mobile_passkey')
        if denied:
            return denied
        cache.delete(f'login_attempts_ip_{ip_address}')
        return _issue_device(request, user, 'passkey', platform, device_name, app_version)


# ── tokens ───────────────────────────────────────────────────────────────────

class RefreshView(PreAuthAPIView):
    throttle_scope = 'refresh'

    def post(self, request):
        data = _data(request)
        if data is None:
            return _err('invalid_request', 400)
        raw = _str(data, 'refresh_token', 200)
        if not raw:
            return _err('invalid_request', 400)
        device, access, refresh_or_reason = rotate(raw)
        if device is None:
            if refresh_or_reason == 'reused':
                security_logger.warning(
                    f'Mobile refresh token reused (grace spent/expired, or superseded) '
                    f'from {_ip(request)}; device revoked')
            return _err(refresh_or_reason, 401)
        return Response(token_response(device, access, refresh_or_reason), status=200)


class LogoutView(MobileAPIView):

    def post(self, request):
        device = request.auth
        device.revoke('signed out')
        return Response({'ok': True}, status=200)


# ── me ───────────────────────────────────────────────────────────────────────

def _links(user):
    """
    Site pages the app links to that depend on who is asking. ``education``:
    the Education dashboard, computed by the same function as the website's
    Home-page dashboard card (``src/view/home.education_dashboard_url``), so
    the app shows it exactly when the site does; None otherwise.
    """
    from src.view.home import education_dashboard_url
    return {'education': education_dashboard_url(user)}


class MeView(MobileAPIView):

    def get(self, request):
        from src.models_feature_flags import FeatureFlag, PageToggle
        from src.permissions import user_is_officer_or_chair
        user = request.user
        device = request.auth
        return Response({
            'user_id': str(user.user_id),
            'username': user.username,
            'name': user.name,
            'display_name': user.get_display_name(),
            'member_type': user.member_type,
            'member_status': user.member_status,
            'is_officer': bool(user.is_officer),
            'is_admin': bool(user.is_admin),
            'is_pledge': bool(user.is_pledge),
            'can_vote': bool(user.can_vote),
            # The announcement page's own gate (officer_required), for the app's
            # "New announcement" quick action. is_officer alone misses chairs.
            'can_post_announcements': user_is_officer_or_chair(user),
            # FeatureFlag fails OPEN for an unseeded flag, exactly as the views do.
            # No 'calendar' flag: that FeatureFlag row is dead (it never gated
            # anything; see prune_dead_feature_flags.py). The calendar's real
            # gate is PageToggle('calendar'), in `pages` below.
            'features': {
                'chats': FeatureFlag.is_feature_enabled('chats'),
                'legislation_voting': FeatureFlag.is_feature_enabled('legislation_voting'),
                'committee_voting': FeatureFlag.is_feature_enabled('committee_voting'),
                'calendar_subscriptions': FeatureFlag.is_feature_enabled('calendar_subscriptions'),
            },
            'pages': {
                'home': PageToggle.is_page_enabled('home'),
                'vote': PageToggle.is_page_enabled('vote'),
                'calendar': PageToggle.is_page_enabled('calendar'),
                'chat_index': PageToggle.is_page_enabled('chat_index'),
            },
            'links': _links(user),
            'device': {
                'device_id': str(device.public_id),
                'platform': device.platform,
                'verified_via': device.verified_via,
            },
        }, status=200)
