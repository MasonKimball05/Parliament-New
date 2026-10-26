"""
Issuing and rotating a device's access/refresh tokens.

Access tokens live 15 minutes, refresh tokens 30 days (sliding), and no refresh
window ever reaches past ``created_at + MOBILE_DEVICE_MAX_AGE_DAYS`` (the
absolute cap; ``MobileDevice.still_allowed`` fails with ``reauth_required``
after it). Refresh rotates both. Only SHA-256 hashes are stored.

Rotation semantics (``rotate``)
-------------------------------
A device has one CURRENT refresh token (``refresh_hash``), at most one
SIBLING (``alt_refresh_hash``, see grace replay) and one PREVIOUS token
(``prev_refresh_hash``) that may be replayed once within ``REFRESH_GRACE``.
Consumed and superseded hashes go to ``retired_refresh_hashes``.

* **Normal rotation** — the current token (or the sibling) is presented:
  a new pair is issued; the presented token becomes the previous one (one
  grace replay, ``REFRESH_GRACE``); the other sibling, if any, and the older
  previous token are retired.
* **Grace replay** — the previous token is presented within ``REFRESH_GRACE``
  and its grace is unused (two concurrent refreshes from the app, or the first
  response was lost): a new pair is issued, the grace is marked used, and the
  current token it displaces is NOT orphaned — it stays usable as the sibling.
  Whichever of the two answers is used first wins; the other is retired.
* **Reuse** — any of: the previous token after its grace was used or expired,
  a retired token (a superseded sibling, an older token). The token was copied:
  the device is revoked (``refresh token reused``) and the member gets a
  security notification. ``rotate`` returns ``reason='reused'``.

So nobody is ever signed out silently: a thief who replays a stolen previous
token within the window gets a pair, but the next refresh by EITHER party
after the other one has moved on revokes the device and alerts the member.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from src.models.mobile import MobileDevice, hash_secret, max_device_age, new_secret

logger = logging.getLogger('admin_actions')

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
REFRESH_GRACE = timedelta(seconds=60)
#: How many consumed/superseded refresh hashes a device remembers.
RETIRED_KEEP = 20


def _pair(now, created_at=None):
    access, refresh = new_secret(), new_secret()
    refresh_expires_at = now + REFRESH_TTL
    cap = (created_at or now) + max_device_age()
    return access, refresh, {
        'access_hash': hash_secret(access),
        'access_expires_at': now + ACCESS_TTL,
        'refresh_hash': hash_secret(refresh),
        'refresh_expires_at': min(refresh_expires_at, cap),
    }


def token_response(device, access, refresh):
    return {
        'access_token': access,
        'access_expires_at': device.access_expires_at.isoformat(),
        'refresh_token': refresh,
        'refresh_expires_at': device.refresh_expires_at.isoformat(),
        'device_id': str(device.public_id),
    }


def create_device(user, verified_via, platform, device_name='', app_version='',
                  login_geo=None, login_geo_suspicious=False):
    """Register a new signed-in install. Returns (device, access, refresh)."""
    now = timezone.now()
    access, refresh, fields = _pair(now)
    device = MobileDevice.objects.create(
        user=user,
        platform=platform if platform in dict(MobileDevice.PLATFORM_CHOICES) else 'ios',
        device_name=(device_name or '')[:100],
        app_version=(app_version or '')[:30],
        auth_fingerprint=user.get_session_auth_hash(),
        verified_via=verified_via,
        last_seen_at=now,
        login_geo=login_geo or {},
        login_geo_suspicious=bool(login_geo_suspicious),
        **fields,
    )
    return device, access, refresh


def _retire(device, *hashes):
    retired = [h for h in (device.retired_refresh_hashes or []) if h not in hashes]
    retired.extend(h for h in hashes if h)
    device.retired_refresh_hashes = retired[-RETIRED_KEEP:]


def _alert_reuse(device):
    """Security notification for the member (and the admin security log)."""
    try:
        from src import security_notifications as sn
        sn.notify_user_security_event(
            device.user,
            'App signed out: sign-in token reused',
            f'A sign-in token of your {device.get_platform_display()} app '
            f'("{device.device_name or "unnamed device"}") was used again after it had been '
            'replaced, so that device was signed out. If you did not just reinstall or '
            'restore the app, change your password.')
        sn.send_security_alert(
            'MOBILE_REFRESH_REUSE', 'high',
            f'Refresh token reuse on mobile device {device.public_id}; device revoked.',
            user=device.user)
    except Exception as exc:   # an alert must never turn a revoke into a 500
        logger.warning('mobile refresh reuse alert failed for device %s: %s', device.pk, exc)


def rotate(raw_refresh):
    """
    Exchange a refresh token for a new pair. See the module docstring for the
    exact semantics.

    Returns (device, access, refresh) on success, or (None, None, reason):
    ``invalid`` (unknown token), ``reused`` (device revoked, member alerted),
    or a ``still_allowed`` reason.
    """
    if not raw_refresh or not isinstance(raw_refresh, str):
        return None, None, 'invalid'
    h = hash_secret(raw_refresh)
    now = timezone.now()
    reused = None
    with transaction.atomic():
        base = MobileDevice.objects.select_for_update().select_related('user')
        device = base.filter(Q(refresh_hash=h) | Q(alt_refresh_hash=h)).first()
        grace = False
        if device is None:
            device = base.filter(prev_refresh_hash=h).first()
            if device is not None:
                if device.prev_refresh_valid_until and device.prev_refresh_valid_until >= now:
                    grace = True
                else:
                    reused = device
            else:
                # Rare path (an unknown token): a JSON containment scan.
                reused = base.filter(retired_refresh_hashes__contains=[h]).first()
                if reused is None:
                    return None, None, 'invalid'
            if reused is not None:
                if reused.revoked_at is not None:
                    return None, None, 'revoked'
                reused.revoke('refresh token reused')
        if reused is None:
            ok, reason = device.still_allowed()
            if not ok:
                return None, None, reason
            access, refresh, fields = _pair(now, device.created_at)
            if grace:
                # Single use; the displaced current token stays live as the sibling.
                _retire(device, device.alt_refresh_hash)
                device.alt_refresh_hash = device.refresh_hash
                device.prev_refresh_valid_until = None
            else:
                other = device.alt_refresh_hash if h == device.refresh_hash else device.refresh_hash
                _retire(device, device.prev_refresh_hash, other if device.alt_refresh_hash else '')
                device.alt_refresh_hash = ''
                device.prev_refresh_hash = h
                device.prev_refresh_valid_until = now + REFRESH_GRACE
            for k, v in fields.items():
                setattr(device, k, v)
            device.last_seen_at = now
            device.save(update_fields=list(fields) + [
                'prev_refresh_hash', 'prev_refresh_valid_until', 'alt_refresh_hash',
                'retired_refresh_hashes', 'last_seen_at'])
    if reused is not None:
        logger.warning('MOBILE REFRESH REUSE: device %s of user %s revoked',
                       reused.pk, reused.user_id)
        _alert_reuse(reused)
        return None, None, 'reused'
    return device, access, refresh
