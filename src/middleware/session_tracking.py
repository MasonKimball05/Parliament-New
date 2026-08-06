"""
Session tracking middleware for Parliament application.
Updates UserSession records on each authenticated request to keep active sessions accurate.
Also performs fingerprint validation to detect potentially stolen sessions.
"""
from django.core.cache import cache
from ..models import UserSession
from src.utils.security_utils import get_client_ip as _get_client_ip
import logging

logger = logging.getLogger('function_calls')


def _get_request_ip(request):
    """Extract the real client IP, respecting BEHIND_CLOUDFLARE setting."""
    return _get_client_ip(request) or ''


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
        # ⚠️ v3.18.7 — ONE interval, and it must stay one. See __call__.
        # Throttle the compare-then-update cycle to once per 5 minutes.
        self.update_interval_seconds = 300

    def __call__(self, request):
        response = self.get_response(request)

        if request.user.is_authenticated and request.session.session_key:
            session_key = request.session.session_key

            # --- Compare, then update, under a SINGLE throttle ---
            #
            # ⚠️ v3.18.7 — THE INVARIANT THIS ENFORCES:
            #     the stored fingerprint is never rewritten without first
            #     being compared against the request doing the rewriting.
            #
            # Until now these were two throttles with different intervals: the
            # record was REWRITTEN every 300 s and COMPARED every 600 s. Both
            # numbers were locally defensible (one described as reducing DB
            # load, the other as avoiding log spam) and nothing said they were
            # coupled — but a write cadence faster than a compare cadence means
            # at least one unexamined baseline refresh per cycle. Concretely,
            # with both keys set together at login and therefore in phase:
            #
            #   t=0    compare (legit), then store legit UA
            #   t=300  update key expires, fp key does not → the record is
            #          rewritten WITH NO COMPARISON. A hijacker's User-Agent
            #          lands in the baseline silently.
            #   t=600  compare finally runs — against the record written at
            #          t=300, i.e. the attacker's own UA. Attacker vs attacker.
            #          Clean. Never detected: not late, at all.
            #
            # So any hijack first arriving in the back half of a cycle was
            # invisible, and the failure mode of a detector is silence, which
            # is indistinguishable from safety.
            #
            # Do NOT "fix" this by setting the two intervals equal — equal
            # intervals still race the moment either is touched, and the next
            # person to tune one has no way to know it is load-bearing. One key
            # and one ordered block makes the invariant structural. It is also
            # CHEAPER: one SELECT plus one update_or_create on the throttled
            # path, and one cache.get per request instead of two.
            check_cache_key = f'session_check_{session_key}'
            if not cache.get(check_cache_key):
                try:
                    stored = UserSession.objects.filter(session_key=session_key).first()
                    if stored is not None:
                        # Compare BEFORE the write below clobbers the baseline.
                        self._check_fingerprint(request, stored)
                except Exception as e:
                    # A failed comparison must not skip the update, but it must
                    # also not be silent — see the bare-except lesson recorded
                    # in CLAUDE.md (v3.18.3).
                    logger.warning(f"Session fingerprint check error: {e}")

                try:
                    UserSession.create_or_update_session(request.user, request)
                except Exception as e:
                    # Don't let session tracking errors break the request
                    logger.warning(f"Failed to update user session: {e}")

                cache.set(check_cache_key, True, self.update_interval_seconds)

        return response

    def _check_fingerprint(self, request, stored):
        """
        Compare the current request's browser/OS against the stored session record.
        Logs a security warning if the browser or OS changes — a strong indicator
        of session hijacking. IP changes alone are not flagged (mobile roaming).

        v3.18.7: takes the already-fetched `stored` row rather than re-querying
        by session_key. The caller must read it before writing (see __call__);
        passing the row in is what makes it impossible to call this function
        against a baseline the caller has already overwritten.

        Detection is log-and-continue by design — a genuine browser upgrade
        changes the User-Agent, and logging out a member for updating Chrome is
        worse than the thing being detected. Confirmed as the intended
        behaviour 08-06-26; if that is ever revisited, the notification path in
        security_notifications.py is the softer option, not session termination.
        """
        session_key = stored.session_key
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
