"""
Session tracking middleware for Parliament application.
Updates UserSession records on each authenticated request to keep active sessions accurate.
Also performs fingerprint validation to detect potentially stolen sessions.
"""
from django.core.cache import cache
from ..models import UserSession
import logging

logger = logging.getLogger('function_calls')


def _get_request_ip(request):
    """Extract the real client IP (rightmost XFF entry, cannot be spoofed through nginx)."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '')


class SessionTrackingMiddleware:
    """
    Middleware to track user sessions on each authenticated request.

    This ensures the Active Sessions display on the user preferences page
    shows accurate session data by updating the UserSession record
    periodically (throttled to reduce database load).

    Also performs session fingerprint validation: if the browser or OS
    detected from the User-Agent changes between requests on the same session,
    a security warning is logged (possible session hijacking). IP changes alone
    are not flagged because mobile users legitimately roam between networks.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Throttle session record updates to once per 5 minutes
        self.update_interval_seconds = 300
        # Only check fingerprint once per 10 minutes per session to avoid spam
        self.fingerprint_check_interval = 600

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_authenticated and request.session.session_key:
            session_key = request.session.session_key

            # --- Fingerprint check (throttled separately from update) ---
            fp_cache_key = f'session_fp_checked_{session_key}'
            if not cache.get(fp_cache_key):
                try:
                    self._check_fingerprint(request, session_key)
                    cache.set(fp_cache_key, True, self.fingerprint_check_interval)
                except Exception as e:
                    logger.warning(f"Session fingerprint check error: {e}")

            # --- Periodic session record update ---
            update_cache_key = f'session_updated_{session_key}'
            if not cache.get(update_cache_key):
                try:
                    UserSession.create_or_update_session(request.user, request)
                    cache.set(update_cache_key, True, self.update_interval_seconds)
                except Exception as e:
                    # Don't let session tracking errors break the request
                    logger.warning(f"Failed to update user session: {e}")

        return response

    def _check_fingerprint(self, request, session_key):
        """
        Compare the current request's browser/OS against the stored session record.
        Logs a security warning if the browser or OS changes — a strong indicator
        of session hijacking. IP changes alone are not flagged (mobile roaming).
        """
        try:
            stored = UserSession.objects.get(session_key=session_key)
        except UserSession.DoesNotExist:
            # No stored record yet — nothing to compare against
            return

        current_ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        current_ip = _get_request_ip(request)
        current_device, current_browser, current_os = UserSession.parse_user_agent(current_ua)

        browser_changed = (
            stored.browser and current_browser != 'Unknown'
            and stored.browser != current_browser
        )
        os_changed = (
            stored.operating_system and current_os != 'Unknown'
            and stored.operating_system != current_os
        )

        if browser_changed or os_changed:
            changes = []
            if browser_changed:
                changes.append(f"browser {stored.browser!r} → {current_browser!r}")
            if os_changed:
                changes.append(f"OS {stored.operating_system!r} → {current_os!r}")

            change_desc = ', '.join(changes)
            logger.warning(
                f"[SESSION FINGERPRINT] Suspicious session change for user "
                f"{request.user.username} (session {session_key[:8]}…): {change_desc}. "
                f"Stored IP: {stored.ip_address}, Current IP: {current_ip}"
            )

            # Log to ActivityLog for admin-v2 visibility
            try:
                from ..models import ActivityLog
                ActivityLog.log_activity(
                    action_type='login',
                    user=request.user,
                    description=(
                        f"Suspicious session fingerprint change: {change_desc}. "
                        f"Original IP: {stored.ip_address}, Current IP: {current_ip}."
                    ),
                    ip_address=current_ip,
                    metadata={'severity': 'high', 'session_key_prefix': session_key[:8]},
                )
            except Exception as e:
                logger.warning(f"Failed to write session fingerprint ActivityLog: {e}")
