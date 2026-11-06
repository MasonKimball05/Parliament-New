"""
Two-factor authentication in the app: the profile page's 2FA card and the
``/accounts/two-factor/...`` pages, natively, through the website's own code
(``src/view/two_factor.py``: ``pending_totp_device``, ``confirm_totp_setup``,
``regenerate_backup_codes``, ``disable_two_factor``;
``src/view/webauthn.check_credential_change_password``).

* ``GET account/two-factor/`` → ``{enabled, method, backup_codes_remaining,
  backup_codes_acknowledged, required, can_disable, has_email}``. ``method`` is
  ``'totp'`` (an authenticator app), ``'backup_codes'`` (codes left but no
  authenticator) or null. ``required`` is the site's 2FA policy for this member
  (``Enforce2FAMiddleware.user_requires_2fa``). Never a secret or a code.
* ``POST account/two-factor/setup/`` → ``{secret, otpauth_url, qr, issuer,
  account_name}``: a NEW pending authenticator key every call (any pending one
  is replaced), so a secret is handed out once and never again. As on the
  website: no password (the website's setup page asks for none), refused when
  2FA is already on (``already_enabled``, 409) and when the account has no email
  address (``email_required``, 409: recovery is by email). ``qr`` is a PNG data
  URI of ``otpauth_url`` for an authenticator on another device.
* ``POST account/two-factor/setup/confirm/`` ``{code}`` → ``{ok, backup_codes}``:
  the website's confirm step; the backup codes are in this response and nowhere
  else, ever (the website shows them once from the session). Wrong codes:
  ``invalid_code`` (400), 5 per 15 minutes per member then ``too_many_attempts``
  (429), on top of django-otp's own per-device throttle. This install becomes a
  second-factor sign-in (``verified_via='password_totp'``): it has just proved
  the code, and a password-only install stops working the moment the member has
  a device (``MobileDevice.still_allowed``) — which is what happens to every
  OTHER password-only install, as every other browser session is sent to the
  website's verify step.
* ``POST account/two-factor/backup-codes/regenerate/`` ``{password}`` →
  ``{ok, backup_codes}``. The website's regenerate asks only for a session that
  passed 2FA; every install that can reach this has (``still_allowed``), and the
  app also asks for the password (the shared credential-change budget below)
  because the codes are shown in the response. Same log line as the website.
* ``POST account/two-factor/disable/`` ``{password}`` → ``{ok}``. The website's
  password re-auth from the same failure budget (``cred_change_attempts_<pk>``,
  5 per 15 minutes, shared with the website's 2FA disable and passkey
  register / remove): ``reauth_failed`` (400), ``reauth_locked`` (429). Refused
  with ``policy_required`` (403) when the policy requires 2FA for this member —
  the website would only send them straight back to setup, and the app would
  then refuse every sign-in (``mfa_setup_required``). Same log line,
  notification and email as the website.

Turning 2FA off or regenerating codes deletes the old devices, which signs out
every OTHER app install (``src/mobile/revocation.py``); this one is kept, as a
password change keeps it. "Forget this device" is the website's remember-me
cookie; the app has none. Recovery (lost authenticator) stays on the website:
it is an emailed link for someone who can't sign in.

Responses carrying a secret or codes are ``Cache-Control: no-store``; neither is
ever logged.
"""
import base64
import logging

from django.conf import settings
from django.core.cache import cache
from django_otp import user_has_device
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.utils.security_utils import get_client_ip

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')

VIA = ' from the app'
SETUP_MAX_FAILURES = 5
SETUP_WINDOW_SECONDS = 15 * 60


class TwoFactorError(exceptions.APIException):
    status_code = 400
    default_code = 'invalid_input'

    def __init__(self, code, message, status=400):
        super().__init__(message, code=code)
        self.status_code = status


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def _no_store(body):
    response = Response(body)
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    response['Pragma'] = 'no-cache'
    return response


def _requires_2fa(user):
    from src.middleware.two_factor import Enforce2FAMiddleware
    return Enforce2FAMiddleware.user_requires_2fa(user)


def _setup_fail_key(user):
    return f'mobile_2fa_setup_failures_{user.pk}'


def _confirm_password(request, data, log_prefix):
    """The website's credential-change password check: same bucket, limits and log lines."""
    from src.view.webauthn import check_credential_change_password
    password = data.get('password')
    if not isinstance(password, str) or not password:
        raise TwoFactorError('reauth_failed', 'Enter your password.')
    if len(password) > 4096:
        raise TwoFactorError('reauth_failed', 'Incorrect password.')
    result = check_credential_change_password(request._request, request.user, password, log_prefix)
    if result == 'rate_limited':
        raise TwoFactorError('reauth_locked', 'Too many failed attempts. Try again in 15 minutes.', 429)
    if result != 'ok':
        raise TwoFactorError('reauth_failed', 'Incorrect password.')


def two_factor_status(user):
    from src.view.two_factor import _get_backup_device
    enabled = user_has_device(user)
    has_totp = TOTPDevice.objects.filter(user=user, confirmed=True).exists()
    backup = _get_backup_device(user)
    required = _requires_2fa(user)
    return {
        'enabled': enabled,
        'method': 'totp' if has_totp else ('backup_codes' if enabled else None),
        'backup_codes_remaining': backup.token_set.count() if backup else 0,
        'backup_codes_acknowledged': bool(user.backup_codes_acknowledged),
        'required': required,
        'can_disable': enabled and not required,
        'has_email': bool(user.email),
    }


class _TwoFactor(MobileAPIView):
    web_page_toggle = False   # the two_factor_* views have no @require_page_enabled


class TwoFactorStatusView(_TwoFactor):
    web_page = 'two_factor_setup'

    def get(self, request):
        return Response(two_factor_status(request.user))


class TwoFactorSetupView(_TwoFactor):
    web_page = 'two_factor_setup'

    def post(self, request):
        from src.utils.qr_svg import render_qr_png_data_uri
        from src.view.two_factor import pending_totp_device
        user = request.user
        if user_has_device(user):
            raise TwoFactorError('already_enabled', 'Two-factor authentication is already on.', 409)
        if not user.email:
            raise TwoFactorError(
                'email_required',
                'Add an email address to your profile first: it is how you recover your account '
                'if you lose your authenticator.', 409)
        device = pending_totp_device(user, fresh=True)
        url = device.config_url
        return _no_store({
            'secret': base64.b32encode(device.bin_key).decode('ascii').rstrip('='),
            'otpauth_url': url,
            'qr': render_qr_png_data_uri(url),
            'issuer': getattr(settings, 'OTP_TOTP_ISSUER', '') or '',
            'account_name': user.username,
        })


class TwoFactorSetupConfirmView(_TwoFactor):
    web_page = 'two_factor_setup'

    def post(self, request):
        from src.view.two_factor import confirm_totp_setup
        user = request.user
        code = _data(request).get('code')
        if not isinstance(code, str) or not code.strip() or len(code) > 32:
            raise TwoFactorError('invalid_input', 'Enter the 6-digit code from your authenticator app.')
        if user_has_device(user):
            raise TwoFactorError('already_enabled', 'Two-factor authentication is already on.', 409)

        fail_key = _setup_fail_key(user)
        failures = cache.get(fail_key, 0)
        if failures >= SETUP_MAX_FAILURES:
            raise TwoFactorError('too_many_attempts', 'Too many wrong codes. Try again in 15 minutes.', 429)

        pending = TOTPDevice.objects.filter(user=user, confirmed=False).order_by('-pk').first()
        if pending is None:
            raise TwoFactorError('setup_not_started', 'That setup expired. Start again.', 409)

        device = request.mobile_device
        codes = confirm_totp_setup(user, pending, code.strip().replace(' ', ''),
                                   keep_mobile_device_id=device.pk)
        if codes is None:
            cache.set(fail_key, failures + 1, SETUP_WINDOW_SECONDS)
            security_logger.warning(
                f'2FA setup (app): wrong code for {user.username} from {get_client_ip(request._request)}')
            raise TwoFactorError('invalid_code', 'That code didn’t match. Check the time on your phone and try again.')

        cache.delete(fail_key)
        # This install just proved the code: it is now a second-factor sign-in
        # (a password-only one stops working once the member has a device).
        if device.verified_via == 'password':
            device.verified_via = 'password_totp'
            device.save(update_fields=['verified_via'])
        # The codes are shown in this response, the app's reveal step.
        _acknowledge(user)
        security_logger.info(
            f'2FA enabled by {user.username} from {get_client_ip(request._request)}{VIA}')
        return _no_store({'ok': True, 'backup_codes': codes})


def _acknowledge(user):
    """The website's reveal page: the member has now seen the codes."""
    user.backup_codes_acknowledged = True
    user.save(update_fields=['backup_codes_acknowledged'])
    cache.delete(f'2fa_status_{user.pk}')


class TwoFactorRegenerateBackupCodesView(_TwoFactor):
    web_page = 'two_factor_regenerate_backup_codes'

    def post(self, request):
        from src.view.two_factor import regenerate_backup_codes
        user = request.user
        if not user_has_device(user):
            raise TwoFactorError('not_enabled', 'Two-factor authentication is not on.', 409)
        _confirm_password(request, _data(request), '2FA backup codes regenerate (app)')
        codes = regenerate_backup_codes(user, via=VIA, keep_mobile_device_id=request.mobile_device.pk)
        _acknowledge(user)
        return _no_store({'ok': True, 'backup_codes': codes})


class TwoFactorDisableView(_TwoFactor):
    web_page = 'two_factor_disable'

    def post(self, request):
        from src.view.two_factor import disable_two_factor
        user = request.user
        if not user_has_device(user):
            raise TwoFactorError('not_enabled', 'Two-factor authentication is not on.', 409)
        if _requires_2fa(user):
            raise TwoFactorError(
                'policy_required',
                'Your chapter requires two-factor authentication for your account, so it can’t be turned off.',
                403)
        _confirm_password(request, _data(request), '2FA disable (app)')
        disable_two_factor(user, get_client_ip(request._request), via=VIA,
                           keep_mobile_device_id=request.mobile_device.pk)
        return Response({'ok': True})

