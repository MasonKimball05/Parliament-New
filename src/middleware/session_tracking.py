"""
Session tracking middleware for Parliament application.
Updates UserSession records on each authenticated request to keep active sessions accurate.
"""
from django.core.cache import cache
from ..models import UserSession
import logging

logger = logging.getLogger('function_calls')


class SessionTrackingMiddleware:
    """
    Middleware to track user sessions on each authenticated request.

    This ensures the Active Sessions display on the user preferences page
    shows accurate session data by updating the UserSession record
    periodically (throttled to reduce database load).
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Throttle updates to once per 5 minutes to reduce DB load
        self.update_interval_seconds = 300

    def __call__(self, request):
        response = self.get_response(request)

        # Update session tracking for authenticated users
        if request.user.is_authenticated and request.session.session_key:
            # Use cache to throttle updates
            cache_key = f'session_updated_{request.session.session_key}'

            if not cache.get(cache_key):
                try:
                    UserSession.create_or_update_session(request.user, request)
                    cache.set(cache_key, True, self.update_interval_seconds)
                except Exception as e:
                    # Don't let session tracking errors break the request
                    logger.warning(f"Failed to update user session: {e}")

        return response
