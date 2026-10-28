"""
DRF authentication for ``/api/mobile/v1/``: ``Authorization: Bearer <access>``.

This class IS the mobile 2FA gate. A device row only exists after the full
sign-in (password + second factor, a passkey, or a password alone when the
member had no second factor and none was required), and every request
re-checks that the device is still allowed (``MobileDevice.still_allowed``:
not revoked, not past its absolute age, user active / not quarantined / not
Removed, password unchanged, and for a password-only device: still no second
factor and none required). There is no
prefix exemption in ``Enforce2FAMiddleware`` for these routes; see
``src/tests/guards/test_mobile_views_use_mobile_auth.py``.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from src.models.mobile import MobileDevice, hash_secret

_SEEN_EVERY = timedelta(minutes=5)


def check_access_token(request, raw):
    """
    The one decision on a raw access token: ``(device, None)`` if it is live
    and the device is still allowed, else ``(None, (message, code))``.

    Remembered on the underlying HttpRequest per token, because one request
    asks twice: the site-wide middlewares (``src/mobile/http.py``
    ``bearer_device``, for lockdown, quarantine, pledge pages...) run before
    DRF, and then this class authenticates the view. Each check is 4 queries
    (the device, plus ``still_allowed``'s second-factor lookups), so asking
    once halves the auth cost of every mobile request. Same request, same
    token, same answer: nothing is skipped, it is only not repeated.
    """
    if not raw or not isinstance(raw, str) or len(raw) > 200:
        return None, ('Invalid token.', 'invalid_token')
    http_request = getattr(request, '_request', request)
    seen = getattr(http_request, '_mobile_token_checks', None)
    if seen is None:
        seen = {}
        http_request._mobile_token_checks = seen
    key = hash_secret(raw)
    if key not in seen:
        seen[key] = _check(key)
    return seen[key]


def _check(access_hash):
    device = (MobileDevice.objects.select_related('user')
              .filter(access_hash=access_hash).first())
    if device is None:
        return None, ('Invalid token.', 'invalid_token')
    if device.access_expires_at <= timezone.now():
        return None, ('Access token expired.', 'token_expired')
    ok, reason = device.still_allowed()
    if not ok:
        return None, ('Signed out.', f'signed_out_{reason}')
    return device, None


class MobileTokenAuthentication(BaseAuthentication):
    keyword = 'Bearer'

    def authenticate(self, request):
        parts = request.META.get('HTTP_AUTHORIZATION', '').split()
        if not parts or parts[0].lower() != 'bearer':
            return None
        if len(parts) != 2:
            raise AuthenticationFailed('Invalid authorization header.', code='invalid_token')
        return self.authenticate_access_token(request, parts[1])

    def authenticate_access_token(self, request, raw):
        """
        ``(user, device)`` for a raw access token, or raise ``AuthenticationFailed``
        with the client-facing code. Shared with ``/m/handoff/``, which also
        accepts the token as a POST form field (see ``src/mobile/handoff.py``).
        """
        device, error = check_access_token(request, raw)
        if error:
            message, code = error
            raise AuthenticationFailed(message, code=code)
        now = timezone.now()
        if not device.last_seen_at or now - device.last_seen_at > _SEEN_EVERY:
            MobileDevice.objects.filter(pk=device.pk).update(last_seen_at=now)
        request.mobile_device = device
        return (device.user, device)

    def authenticate_header(self, request):
        return self.keyword


CHECKIN_KEY_HEADER = 'HTTP_X_CHECKIN_KEY'


def check_checkin_key(request, raw):
    """
    The device behind a location check-in key (``X-Checkin-Key``), if it is
    still allowed, else None. Same ``still_allowed`` rules as an access token:
    revoking the phone, a password change or a removed member all end it.
    """
    if not raw or not isinstance(raw, str) or len(raw) > 200:
        return None
    http_request = getattr(request, '_request', request)
    if hasattr(http_request, '_mobile_checkin_device'):
        return http_request._mobile_checkin_device
    device = (MobileDevice.objects.select_related('user')
              .filter(checkin_key_hash=hash_secret(raw)).first())
    if device is not None and not device.still_allowed()[0]:
        device = None
    http_request._mobile_checkin_device = device
    return device


class CheckinKeyAuthentication(BaseAuthentication):
    """
    For ``events/<id>/checkin/location/`` only (``CheckinKeyAPIView``). The
    app's background geofence task runs while the phone may be locked, when
    the Keychain won't release the access/refresh tokens (they are
    WHEN_UNLOCKED). It holds this narrower key instead, which can do nothing
    but check its member in, inside an area, inside a window.
    """

    def authenticate(self, request):
        raw = request.META.get(CHECKIN_KEY_HEADER, '')
        if not raw:
            return None
        device = check_checkin_key(request, raw)
        if device is None:
            raise AuthenticationFailed('Invalid check-in key.', code='invalid_checkin_key')
        request.mobile_device = device
        return (device.user, device)

    def authenticate_header(self, request):
        return 'Checkin-Key'
