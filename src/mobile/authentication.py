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
        if not raw or not isinstance(raw, str) or len(raw) > 200:
            raise AuthenticationFailed('Invalid token.', code='invalid_token')
        device = (MobileDevice.objects.select_related('user')
                  .filter(access_hash=hash_secret(raw)).first())
        if device is None:
            raise AuthenticationFailed('Invalid token.', code='invalid_token')
        if device.access_expires_at <= timezone.now():
            raise AuthenticationFailed('Access token expired.', code='token_expired')
        ok, reason = device.still_allowed()
        if not ok:
            raise AuthenticationFailed('Signed out.', code=f'signed_out_{reason}')
        now = timezone.now()
        if not device.last_seen_at or now - device.last_seen_at > _SEEN_EVERY:
            MobileDevice.objects.filter(pk=device.pk).update(last_seen_at=now)
        request.mobile_device = device
        return (device.user, device)

    def authenticate_header(self, request):
        return self.keyword
