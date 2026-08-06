"""
Maintenance Mode Middleware

Blocks all access to the site when maintenance_mode feature flag is enabled,
except for admin users who can still access the site.
"""

from django.shortcuts import render
from django.http import HttpResponse
import logging

logger = logging.getLogger(__name__)


class MaintenanceModeMiddleware:
    """
    Middleware that checks if maintenance mode is enabled and blocks access
    to non-admin users, showing a maintenance page instead.
    """

    # Paths that should always be accessible (even in maintenance mode)
    EXEMPT_PATHS = [
        '/admin/',  # Django admin (for admins to disable maintenance mode)
        '/static/',  # Static files
        '/media/',  # Media files
        '/api/health-check/',  # Health check endpoint
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if maintenance mode is enabled
        if self._is_maintenance_mode_enabled():
            # Allow exempt paths
            if self._is_exempt_path(request.path):
                return self.get_response(request)

            # Allow admin users
            if request.user.is_authenticated and getattr(request.user, 'is_admin', False):
                return self.get_response(request)

            # Increment blocked request counter
            self._increment_blocked_count()

            # Show maintenance page
            logger.info(f"Maintenance mode: blocked access to {request.path} from {self._get_client_ip(request)}")
            return self._maintenance_response(request)

        return self.get_response(request)

    def _is_maintenance_mode_enabled(self):
        """Check if maintenance mode feature flag is enabled"""
        try:
            from src.models_feature_flags import FeatureFlag
            return FeatureFlag.is_feature_enabled('maintenance_mode')
        except Exception as e:
            # If we can't check the flag, assume maintenance mode is OFF
            logger.error(f"Error checking maintenance mode flag: {e}")
            return False

    def _is_exempt_path(self, path):
        """Check if the path is exempt from maintenance mode"""
        for exempt in self.EXEMPT_PATHS:
            if path.startswith(exempt):
                return True
        return False

    def _get_client_ip(self, request):
        """
        Get client IP address.

        v3.18.8: delegates instead of reimplementing — the inline version
        ignored BEHIND_CLOUDFLARE and logged the Cloudflare edge. Every other
        middleware in this package already imported the helper; this one was
        the odd one out. See the note in models/activity.py.
        """
        from src.utils.security_utils import get_client_ip
        return get_client_ip(request) or 'unknown'

    def _increment_blocked_count(self):
        """Increment the blocked request counter for admin stats"""
        try:
            from django.core.cache import cache
            cache_key = 'maintenance_blocked_count'
            current = cache.get(cache_key, 0)
            cache.set(cache_key, current + 1, 86400)  # Cache for 24 hours
        except Exception:
            pass  # Don't fail if cache is unavailable

    def _maintenance_response(self, request):
        """Return the maintenance page response"""
        try:
            return render(request, 'maintenance.html', status=503)
        except Exception:
            # Fallback to simple HTML if template doesn't exist
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Site Maintenance</title>
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        min-height: 100vh;
                        margin: 0;
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        text-align: center;
                        padding: 20px;
                    }
                    .container {
                        max-width: 500px;
                    }
                    h1 { font-size: 2.5rem; margin-bottom: 1rem; }
                    p { font-size: 1.1rem; opacity: 0.9; line-height: 1.6; }
                    .icon { font-size: 4rem; margin-bottom: 1rem; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="icon">🔧</div>
                    <h1>We'll be back soon!</h1>
                    <p>We're currently performing scheduled maintenance to improve your experience. Please check back in a few minutes.</p>
                </div>
            </body>
            </html>
            """
            return HttpResponse(html, status=503, content_type='text/html')
