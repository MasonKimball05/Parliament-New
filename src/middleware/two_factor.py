"""
Middleware to enforce Two-Factor Authentication based on configurable policy
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.core import signing
from django_otp import user_has_device, login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class Enforce2FAMiddleware:
    """
    Enforce 2FA based on global policy and individual requirements.

    Policy modes (stored in SiteSetting '2fa_policy_mode'):
    - 'none': No 2FA required
    - 'admins_only': Only admins require 2FA
    - 'officers_and_admins': Officers and admins require 2FA
    - 'all_members': All active members require 2FA
    - 'custom': Only individually marked users require 2FA

    Individual overrides (TwoFactorRequirement model):
    - 'required': User must have 2FA regardless of policy
    - 'exempt': User is exempt from 2FA regardless of policy
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Allow unauthenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Paths that don't require 2FA (login, logout, 2FA setup)
        exempt_paths = [
            '/login/',
            '/logout/',
            '/accounts/two-factor/setup/',
            '/accounts/two-factor/qrcode/',
            '/accounts/two-factor/verify/',
            # NOTE: /accounts/two-factor/disable/ is deliberately NOT exempt
            # (v3.13.2): a session that passed the password check but not the
            # TOTP step must not be able to strip 2FA from the account.
            '/accounts/two-factor/dismiss/',
            '/accounts/two-factor/recovery/',
            '/accounts/two-factor/recovery-confirm/',
            '/accounts/passkeys/authenticate/',  # passkey login endpoints
            '/static/',
            '/media/',
            '/exportable_media/',  # media assets — 2FA-redirecting an <img> just breaks the image (v3.14.1)
            # Token-authenticated API only — the DRF app under /api/v1/ (incl.
            # the honeypot export) authenticates per-request and handles auth
            # itself. This deliberately is NOT the broad '/api/' prefix: many
            # SESSION-cookie-authenticated AJAX endpoints live under /api/...
            # (roles, notifications, chat, slating, service-hours, /api/debug/*,
            # and /api/token/* which can MINT an API token). Exempting all of
            # /api/ let a password-only, 2FA-UNVERIFIED session reach them and
            # skip the verify-step enforcement below — partially defeating 2FA.
            # (07-22 auth security sweep, Finding A.)
            '/api/v1/',
            '/api/health-check/',  # liveness probe — must never require a second factor
        ]

        # Check if current path is exempt
        for path in exempt_paths:
            if request.path.startswith(path):
                return self.get_response(request)

        # Also exempt the login URL by name
        try:
            if request.path == reverse('login'):
                return self.get_response(request)
        except Exception:
            pass

        # Skip 2FA enforcement for active admin impersonation sessions
        if getattr(request, 'session', {}).get('_impersonating_original_user_id'):
            return self.get_response(request)

        # Check if user requires 2FA
        requires_2fa = self.user_requires_2fa(request.user)

        # One device lookup per request (was queried twice — once here, once
        # for the verified-session check below).
        has_device = user_has_device(request.user)

        # If 2FA is required but not set up, redirect to setup page
        if requires_2fa and not has_device:
            # Check if user has dismissed the prompt recently (within 1 hour)
            dismiss_until = getattr(request, 'session', {}).get('2fa_setup_dismissed_until')
            if dismiss_until:
                try:
                    dismiss_time = timezone.datetime.fromisoformat(dismiss_until)
                    if timezone.is_naive(dismiss_time):
                        dismiss_time = timezone.make_aware(dismiss_time)
                    if timezone.now() < dismiss_time:
                        # Still within dismissal period, allow access
                        return self.get_response(request)
                except (ValueError, TypeError):
                    # Invalid dismiss time, clear it
                    if hasattr(request, 'session'):
                        del request.session['2fa_setup_dismissed_until']

            if request.path != '/accounts/two-factor/setup/':
                return redirect('two_factor_setup')

        # If 2FA is set up but not verified this session, enforce the verify
        # step — regardless of policy (v3.13.2, specced in v3.5.1): a user who
        # voluntarily enabled 2FA expects it to actually protect their account,
        # and an attacker must not be able to skip the TOTP step just because
        # the global policy doesn't mandate 2FA for this user.
        if has_device:
            if not request.user.is_verified() and request.path != '/accounts/two-factor/verify/':
                # Passkey login sets this flag and counts as full authentication
                if getattr(request, 'session', {}).get('webauthn_authenticated'):
                    pass  # passkey-authenticated — bypass TOTP step
                # Check for a valid "remember this device" cookie before forcing verify
                elif self._check_remember_cookie(request):
                    pass  # auto-verified via cookie — fall through
                else:
                    return redirect('two_factor_verify')

        return self.get_response(request)

    def _check_remember_cookie(self, request):
        """
        Check the remember-device cookie. If valid and the device still exists,
        auto-complete OTP login for this session and return True.
        """
        from src.view.two_factor import _REMEMBER_COOKIE_NAME, _REMEMBER_COOKIE_SALT, _REMEMBER_DAYS
        cookie = request.COOKIES.get(_REMEMBER_COOKIE_NAME)
        if not cookie:
            return False
        try:
            signer = signing.TimestampSigner(salt=_REMEMBER_COOKIE_SALT)
            value = signer.unsign(cookie, max_age=_REMEMBER_DAYS * 86400)
            user_pk, device_pk = value.split(':', 1)
            if int(user_pk) != request.user.pk:
                return False
            device = TOTPDevice.objects.filter(pk=int(device_pk), user=request.user, confirmed=True).first()
            if not device:
                return False
            otp_login(request, device)
            logger.debug(f'2FA auto-verified via remember cookie for {request.user.username}')
            return True
        except (signing.BadSignature, signing.SignatureExpired, ValueError, TypeError):
            return False

    def user_requires_2fa(self, user):
        """
        Check if user requires 2FA based on policy and individual settings.
        """
        # Import here to avoid circular imports
        from src.models import TwoFactorRequirement
        from src.models_feature_flags import SiteSetting

        # Check individual requirement first (takes precedence)
        try:
            req = user.two_factor_requirement
            if req.requirement == 'exempt':
                return False
            if req.requirement == 'required':
                return True
        except TwoFactorRequirement.DoesNotExist:
            pass

        # Fall back to global policy
        policy = SiteSetting.get_setting('2fa_policy_mode', 'none')

        if policy == 'none':
            return False
        elif policy == 'admins_only':
            return user.is_admin
        elif policy == 'officers_and_admins':
            return user.is_officer or user.is_admin
        elif policy == 'all_members':
            return True
        elif policy == 'custom':
            # In custom mode, only users with explicit 'required' need 2FA
            # (already handled above, so return False here)
            return False

        return False
