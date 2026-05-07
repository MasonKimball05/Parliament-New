"""
Emergency lockdown middleware for Parliament.
When activated, blocks all access except from whitelisted IPs.
"""
from django.shortcuts import render
from django.http import HttpResponse
from django.urls import reverse
from django.conf import settings
from src.utils.security_utils import get_client_ip as _get_client_ip
import logging

logger = logging.getLogger('admin_actions')


class EmergencyLockdownMiddleware:
    """
    Middleware that enforces emergency lockdown mode.
    When lockdown is active, all non-whitelisted users see a maintenance page.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Paths that are always accessible (for static files, health checks)
        self.always_allowed = [
            '/static/',
            '/media/',
            '/health/',
            '/favicon.ico',
        ]

    def __call__(self, request):
        # Skip check for always-allowed paths
        if any(request.path.startswith(path) for path in self.always_allowed):
            return self.get_response(request)

        # Check if lockdown is active
        from src.models import SystemLockdown
        try:
            lockdown = SystemLockdown.get_instance()
        except Exception as e:
            # If we can't check lockdown status, let the request through
            logger.warning(f"Could not check lockdown status: {e}")
            return self.get_response(request)

        if lockdown.is_active:
            ip_address = self.get_client_ip(request)

            # Check if IP is whitelisted
            if not lockdown.is_ip_whitelisted(ip_address):
                logger.warning(f"LOCKDOWN: Blocked access from {ip_address} to {request.path}")

                # Allow access to lockdown page itself
                if request.path == '/lockdown/':
                    return render(request, 'lockdown.html', {
                        'message': lockdown.message,
                        'reason': lockdown.reason if request.user.is_authenticated and request.user.is_admin else None,
                    }, status=503)

                # For authenticated admins, let them through to the deactivation page
                if (request.user.is_authenticated and
                    hasattr(request.user, 'is_admin') and
                    request.user.is_admin and
                    request.path.startswith('/admin-v2/')):
                    # Allow admin access from any IP for emergency deactivation
                    pass
                else:
                    # Block and redirect to lockdown page
                    return render(request, 'lockdown.html', {
                        'message': lockdown.message,
                    }, status=503)

        return self.get_response(request)

    def get_client_ip(self, request):
        """Get the client's IP address, respecting BEHIND_CLOUDFLARE setting."""
        return _get_client_ip(request) or 'unknown'
