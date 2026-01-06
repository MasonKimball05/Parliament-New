"""
Middleware to enforce Two-Factor Authentication for admin and officer users
"""
from django.shortcuts import redirect
from django.urls import reverse
from django.conf import settings
from django_otp import user_has_device


class Enforce2FAMiddleware:
    """
    Enforce 2FA for admin and officer users

    - Admins and officers must set up 2FA before accessing the system
    - Redirects to 2FA setup page if not configured
    - Allows access to login, logout, and 2FA setup pages
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Allow unauthenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Paths that don't require 2FA (login, logout, 2FA setup)
        exempt_paths = [
            reverse('login'),
            reverse('logout'),
            '/accounts/two-factor/setup/',
            '/accounts/two-factor/qrcode/',
            '/accounts/two-factor/verify/',
            '/static/',
            '/media/',
        ]

        # Check if current path is exempt
        for path in exempt_paths:
            if request.path.startswith(path):
                return self.get_response(request)

        # Check if user requires 2FA
        requires_2fa = False

        if settings.REQUIRE_2FA_FOR_ADMINS and request.user.is_admin:
            requires_2fa = True

        if settings.REQUIRE_2FA_FOR_OFFICERS and request.user.is_officer:
            requires_2fa = True

        # If 2FA is required but not set up, redirect to setup page
        if requires_2fa and not user_has_device(request.user):
            if request.path != '/accounts/two-factor/setup/':
                return redirect('/accounts/two-factor/setup/')

        # If 2FA is required and set up, but not verified this session
        if requires_2fa and user_has_device(request.user):
            if not request.user.is_verified() and request.path != '/accounts/two-factor/verify/':
                return redirect('/accounts/two-factor/verify/')

        return self.get_response(request)
