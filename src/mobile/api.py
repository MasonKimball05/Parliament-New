"""
Base views, error shape and throttles for ``/api/mobile/v1/``.

Every mobile view subclasses one of two bases:

* ``MobileAPIView`` — Bearer-authenticated (``MobileTokenAuthentication``),
  ``IsAuthenticated``, throttled per user under its own ``mobile`` scope
  (``MOBILE_API_THROTTLE_RATE``, default 1200/hour) instead of the site-wide
  ``UserRateThrottle``. The default for anything new.
* ``PreAuthAPIView`` — the handful of sign-in endpoints that run before the app
  has a token. No authentication classes at all (so no session, no CSRF), and
  per-IP + per-device-id throttles on top of the account lockouts.

``src/tests/guards/test_mobile_views_use_mobile_auth.py`` fails the build if a
view under the ``mobile`` namespace is neither, or is a pre-auth view that
isn't on its allowlist.

Errors are always ``{"error": "<code>", ...}`` (``mobile_exception_handler``),
the same shape the middlewares use (``src/mobile/http.py``).
"""
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle
from rest_framework.views import APIView, exception_handler as drf_exception_handler

from src.mobile.authentication import CheckinKeyAuthentication, MobileTokenAuthentication
from src.utils.security_utils import get_client_ip


def mobile_exception_handler(exc, context):
    """DRF's handler, reshaped to ``{"error": code, "detail": message}``."""
    # Django's Http404 / PermissionDenied: DRF converts them internally but
    # hands back the original exception, which has no code. Convert here.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, exceptions.Throttled):
        response.data = {'error': 'throttled', 'retry_after': int(exc.wait or 1)}
    elif isinstance(exc, exceptions.APIException):
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else 'invalid_request'
        detail = exc.detail if isinstance(exc.detail, str) else 'Invalid request.'
        response.data = {'error': code, 'detail': str(detail)}
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    return response


# ── Throttles ────────────────────────────────────────────────────────────────

#: Requests per scope per IP / per device id. Overridable with the
#: MOBILE_AUTH_THROTTLE_RATES setting ({scope: 'N/period'}).
DEFAULT_RATES = {
    'login': '10/min',
    'verify': '10/min',
    'passkey': '20/min',
    'refresh': '30/min',
}

_DEVICE_ID_OK = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')


class _MobileAuthThrottle(SimpleRateThrottle):
    """Keyed by the view's ``throttle_scope`` plus an identity (see subclasses)."""

    kind = ''

    def __init__(self):
        # The scope comes from the view, so the rate is resolved in allow_request.
        pass

    def get_rate_for(self, scope):
        rates = {**DEFAULT_RATES, **getattr(settings, 'MOBILE_AUTH_THROTTLE_RATES', {})}
        return rates.get(scope)

    def identity(self, request):
        raise NotImplementedError

    def allow_request(self, request, view):
        self.scope = getattr(view, 'throttle_scope', None)
        if not self.scope:
            return True
        self.rate = self.get_rate_for(self.scope)
        if not self.rate:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        ident = self.identity(request)
        if ident is None:
            return None
        return f'throttle_mobile_{self.kind}_{self.scope}_{ident}'


class MobileAuthIPThrottle(_MobileAuthThrottle):
    """Per client IP, via the one IP helper (``get_client_ip``)."""
    kind = 'ip'

    def identity(self, request):
        return get_client_ip(request._request) or 'unknown'


class MobileAuthDeviceThrottle(_MobileAuthThrottle):
    """Per ``X-Device-Id`` header, when the app sends one (an install id)."""
    kind = 'dev'

    def identity(self, request):
        raw = request.META.get('HTTP_X_DEVICE_ID', '')
        if not raw:
            return None
        if not _DEVICE_ID_OK.match(raw):
            return 'malformed'   # one shared bucket for junk values
        return raw


class MobileUserThrottle(UserRateThrottle):
    """
    Per signed-in member, for every Bearer-authenticated view. The app spends
    requests the website doesn't (a handoff code per web-view open, ``me/``,
    websocket tickets, polling), so it gets its own bucket instead of sharing
    DRF's site-wide ``user`` 300/hour.
    """
    scope = 'mobile'
    cache_format = 'throttle_%(scope)s_user_%(ident)s'   # apart from the pre-auth keys

    def get_rate(self):
        return getattr(settings, 'MOBILE_API_THROTTLE_RATE', '1200/hour')


# ── Web-page gates ───────────────────────────────────────────────────────────

def check_web_page(user, url_name, toggle=True):
    """
    Refuse the request if the website would refuse ``user`` the page
    ``url_name``: its PageToggle is off (``@require_page_enabled``; skipped
    with ``toggle=False`` for a web page that has no such decorator), or a
    pledge restriction blocks it (``PledgePageRestrictionMiddleware``).

    Both rules key on the WEB page's url_name, so a mobile route named
    ``mobile_home`` is not covered by a toggle or restriction on ``home`` unless
    the view says which page it mirrors (Phase 3). Same helpers as the web, so
    the same fail-open/fail-closed behaviour.
    """
    from src.models.education import PledgePageRestriction
    from src.models_feature_flags import PageToggle
    if toggle and not PageToggle.is_page_enabled(url_name):
        raise exceptions.PermissionDenied('This page is turned off.', code='page_disabled')
    if user.is_pledge:
        phase = getattr(user, 'pledge_phase', None) or 'all'
        if not PledgePageRestriction.is_allowed(url_name, phase):
            raise exceptions.PermissionDenied('Not available to pledges yet.',
                                              code='pledge_restricted')


# ── Bases ────────────────────────────────────────────────────────────────────

class MobileAPIView(APIView):
    authentication_classes = [MobileTokenAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [MobileUserThrottle]

    #: url_name of the web page this endpoint is the app's version of, if any.
    #: Its page toggle and pledge restriction apply here too (check_web_page).
    #: The guard test requires it on every view that isn't app plumbing.
    web_page = None
    #: False when that web page has no @require_page_enabled (so the app
    #: doesn't honour a toggle the site ignores).
    web_page_toggle = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.web_page:
            check_web_page(request.user, self.web_page, toggle=self.web_page_toggle)

    def get_exception_handler(self):
        return mobile_exception_handler


class CheckinKeyAPIView(APIView):
    """
    The location check-in endpoint: authenticated by the device's check-in key
    (``CheckinKeyAuthentication``) instead of a Bearer token, so the app's
    background task can call it while the phone is locked. Nothing else uses it.
    """
    authentication_classes = [CheckinKeyAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [MobileUserThrottle]
    web_page = None
    web_page_toggle = True

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if self.web_page:
            check_web_page(request.user, self.web_page, toggle=self.web_page_toggle)

    def get_exception_handler(self):
        return mobile_exception_handler


class PreAuthAPIView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [MobileAuthIPThrottle, MobileAuthDeviceThrottle]
    throttle_scope = None   # set per view

    def get_exception_handler(self):
        return mobile_exception_handler
