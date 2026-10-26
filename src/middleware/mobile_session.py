"""
Mobile plan, Phase 1: web-view sessions live and die with their app device.

A session created by ``/m/handoff/`` (``src/mobile/handoff.py``) carries
``mobile_device_id``. On every request this middleware loads that device (one
narrow query, by primary key) and, if the device has been revoked or expired,
belongs to someone else, or ``still_allowed`` fails for the session user
(deactivated, quarantined, Removed, password changed), logs the session out:
a 401 JSON body for ``/api/`` paths, otherwise a redirect to the login page —
except on ``/m/handoff/`` itself: there the dead session is logged out and the
request carries on, so the app's new handoff proceeds (or gets its bare 403).
A web view must never be shown a password form.

Websockets get the same check from ``SecondFactorWebsocketMiddleware``
(``src/middleware/two_factor.py``) via ``device_session_ok_for``.

Sessions without the key are untouched and cost nothing extra.

ORDER (settings.MIDDLEWARE): directly after ``AuthenticationMiddleware`` (it
needs ``request.user``) and before ``OTPMiddleware`` / ``Enforce2FAMiddleware``,
so a dead device's session is logged out outright instead of being bounced to
the 2FA verify page, and before every middleware that acts on the user.

It also records ``request.in_mobile_app_ua`` when the layout flag below was
decided by the User-Agent, and adds ``Vary: User-Agent`` to that response.
"""
from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.cache import patch_vary_headers

SESSION_KEY = 'mobile_device_id'
APP_UA_MARKER = 'ParliamentApp/'


HANDOFF_PATH = '/m/handoff/'


def device_session_ok_for(user, session):
    """
    False when ``session`` carries ``mobile_device_id`` and that device no
    longer vouches for it. Sync (it queries). ``session`` is a SessionStore or
    a dict. Sessions without the key are always ok and cost nothing.
    """
    from src.models.mobile import MobileDevice

    if session is None or not getattr(user, 'is_authenticated', False):
        return True  # nothing signed in to log out
    if SESSION_KEY not in session:
        return True
    device_id = session.get(SESSION_KEY)
    device = (MobileDevice.objects
              .only('id', 'user_id', 'revoked_at', 'refresh_expires_at', 'auth_fingerprint',
                    'verified_via', 'created_at')
              .filter(pk=device_id).first()) if isinstance(device_id, int) else None
    if device is None or str(device.user_id) != str(user.pk):
        return False
    ok, _reason = device.still_allowed(user)
    return ok


def device_session_ok(request):
    """False when the session's mobile device no longer vouches for it."""
    return device_session_ok_for(request.user, request.session)


class MobileSessionGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session = getattr(request, 'session', None)
        if (session is not None and settings.SESSION_COOKIE_NAME in request.COOKIES
                and SESSION_KEY in session and not device_session_ok(request)):
            logout(request)
            if request.path == HANDOFF_PATH:
                # The app is handing off a NEW sign-in; let the view decide.
                return self._respond(request)
            if request.path.startswith('/api/'):
                return JsonResponse(
                    {'detail': 'Signed out.', 'code': 'mobile_device_signed_out'}, status=401)
            return redirect('login')
        return self._respond(request)

    def _respond(self, request):
        response = self.get_response(request)
        if getattr(request, 'in_mobile_app_ua', False):
            patch_vary_headers(response, ('User-Agent',))
        return response


def in_mobile_app(request):
    """
    Context processor: ``in_mobile_app`` — the page is shown inside the app's
    web view, so ``base.html`` hides the site's own navbar and footer (the app
    has native navigation).

    ⚠️ LAYOUT ONLY. The User-Agent half of this is trivially spoofable. This
    flag must NEVER relax or skip a security check (2FA, CSRF, permissions,
    CSP, rate limits, anything). Security decisions about app sessions use
    the device behind ``mobile_device_id``, checked above.
    """
    session = getattr(request, 'session', None)
    if session is not None and SESSION_KEY in session:
        return {'in_mobile_app': True}
    # The response now depends on the User-Agent: tell caches.
    request.in_mobile_app_ua = True
    return {'in_mobile_app': APP_UA_MARKER in request.META.get('HTTP_USER_AGENT', '')}
