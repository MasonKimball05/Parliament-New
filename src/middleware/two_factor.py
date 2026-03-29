"""
Middleware to enforce Two-Factor Authentication based on configurable policy
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django_otp import user_has_device
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
            '/accounts/two-factor/disable/',
            '/accounts/two-factor/dismiss/',
            '/static/',
            '/media/',
            '/api/',  # API endpoints should handle auth separately
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

        # Check if user requires 2FA
        requires_2fa = self.user_requires_2fa(request.user)

        # If 2FA is required but not set up, redirect to setup page
        if requires_2fa and not user_has_device(request.user):
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

        # If 2FA is required and set up, but not verified this session
        if requires_2fa and user_has_device(request.user):
            if not request.user.is_verified() and request.path != '/accounts/two-factor/verify/':
                return redirect('two_factor_verify')

        return self.get_response(request)

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
