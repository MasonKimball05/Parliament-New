"""
Helpers the site-wide middlewares use to answer ``/api/mobile/`` in JSON.

The lockdown, maintenance, forced-password-change, quarantine, pledge-page and
IP-blacklist middlewares answer the web with HTML pages and redirects. The app
can't follow a redirect to ``/login/`` or render a maintenance page, so for
paths under ``MOBILE_API_PREFIX`` each of them returns ``mobile_json_error``
instead, with the same status semantics.

Those middlewares run BEFORE DRF authenticates the request, so ``request.user``
there is the SESSION user (anonymous for a Bearer request). ``effective_user``
resolves the Bearer device's user for them — read-only, cached on the request,
and never assigned to ``request.user`` (so ``Enforce2FAMiddleware``, which keys
off the session user, is unaffected; see the module docstring of
``src/mobile/authentication.py``). DRF's ``MobileTokenAuthentication`` still
makes the real, authoritative decision on the view.
"""
from django.http import JsonResponse
from django.utils import timezone

MOBILE_API_PREFIX = '/api/mobile/'

_NO_STORE = 'no-store, no-cache, must-revalidate, private'


def is_mobile_api(request):
    return request.path.startswith(MOBILE_API_PREFIX)


def mobile_json_error(error, status, **extra):
    """``{"error": <code>, ...}`` with no-store headers (never cache an error)."""
    response = JsonResponse({'error': error, **extra}, status=status)
    response['Cache-Control'] = _NO_STORE
    response['Pragma'] = 'no-cache'
    return response


def bearer_device(request):
    """
    The live ``MobileDevice`` behind this request's ``Authorization: Bearer``
    header, or None (no header, unknown/expired token, or the device is no
    longer allowed). Cached on the request; issues at most one query.
    """
    if hasattr(request, '_mobile_bearer_device'):
        return request._mobile_bearer_device
    device = None
    parts = request.META.get('HTTP_AUTHORIZATION', '').split()
    if len(parts) == 2 and parts[0].lower() == 'bearer':
        from src.models.mobile import MobileDevice, hash_secret
        candidate = (MobileDevice.objects.select_related('user')
                     .filter(access_hash=hash_secret(parts[1])).first())
        if (candidate is not None and candidate.access_expires_at > timezone.now()
                and candidate.still_allowed()[0]):
            device = candidate
    request._mobile_bearer_device = device
    return device


def effective_user(request):
    """
    Who a middleware should treat as signed in: on ``/api/mobile/`` with a
    valid Bearer token, that device's user; otherwise ``request.user``.
    """
    if is_mobile_api(request):
        device = bearer_device(request)
        if device is not None:
            return device.user
    return getattr(request, 'user', None)
