"""
Mobile app devices and short-lived auth tickets (mobile plan, Phase 1).

One ``MobileDevice`` row per app install that has signed in. It holds the
device's tokens as SHA-256 hashes only (never the tokens themselves), its Expo
push token, and what it takes to decide on EVERY request whether the device is
still allowed:

* ``revoked_at`` is empty, the refresh window hasn't ended and the device is
  younger than ``MOBILE_DEVICE_MAX_AGE_DAYS`` (an absolute cap: rotation slides
  the refresh window, never past it);
* the user is still active, not quarantined, not Removed;
* ``auth_fingerprint`` still equals ``user.get_session_auth_hash()``;
* a device that signed in with a password ALONE (``verified_via='password'``,
  the member had no second factor then) stops working as soon as the member
  has a confirmed django-otp device or the 2FA policy requires one: it has to
  sign in again with the second factor.

The fingerprint is the "epoch" from the plan's security review, done without a
counter: Django's session-auth hash is derived from the password hash, so a
password change by ANY path (the member, a forced reset, an admin, the shell)
signs every app out with no hook to forget. Events that don't touch the
password (2FA removed, a passkey deleted, sign-out-others) revoke explicitly —
see ``src/mobile/revocation.py``.

``MobileAuthTicket`` holds every short-lived, single-use secret the mobile flow
needs: the 2FA ticket between password and code, passkey challenges, web-view
handoff codes, websocket tickets and vote re-auth grants. Same rule: only the
hash is stored.
"""
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_secret(raw):
    """SHA-256 hex of a token. Tokens are 256-bit random, so no salt/KDF needed."""
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def new_secret():
    """A fresh 256-bit URL-safe token."""
    return secrets.token_urlsafe(32)


def max_device_age():
    """``MOBILE_DEVICE_MAX_AGE_DAYS`` as a timedelta (absolute sign-in lifetime)."""
    return timedelta(days=getattr(settings, 'MOBILE_DEVICE_MAX_AGE_DAYS', 90))


def _second_factor_now_expected(user):
    """
    True when a password-only sign-in is no longer enough for ``user``: they
    have a confirmed django-otp device (TOTP / backup codes), or the 2FA policy
    (``Enforce2FAMiddleware.user_requires_2fa``) requires one.

    Only asked for ``verified_via='password'`` devices, so the per-request cost
    for everyone else is zero. A passkey alone is not a django-otp device and,
    exactly as on the website and in the app's own password sign-in, does not
    make the password step insufficient.
    """
    from django_otp import user_has_device
    from src.middleware.two_factor import Enforce2FAMiddleware
    return user_has_device(user) or Enforce2FAMiddleware.user_requires_2fa(user)


class MobileDevice(models.Model):
    PLATFORM_CHOICES = [('ios', 'iOS'), ('android', 'Android')]
    VIA_CHOICES = [
        ('password', 'Password (no 2FA device)'),
        ('password_totp', 'Password + authenticator code'),
        ('password_backup', 'Password + backup code'),
        ('passkey', 'Passkey'),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mobile_devices')
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    device_name = models.CharField(max_length=100, blank=True)
    app_version = models.CharField(max_length=30, blank=True)
    push_token = models.CharField(max_length=255, blank=True, db_index=True)

    access_hash = models.CharField(max_length=64, unique=True)
    access_expires_at = models.DateTimeField()
    refresh_hash = models.CharField(max_length=64, unique=True)
    refresh_expires_at = models.DateTimeField()
    # The refresh token this one replaced, honoured for a short grace window so
    # a dropped response doesn't lock the member out (review finding 3).
    # Grace is single use: replaying the previous token clears
    # prev_refresh_valid_until, so a second replay revokes (src/mobile/tokens.py).
    prev_refresh_hash = models.CharField(max_length=64, blank=True, db_index=True)
    prev_refresh_valid_until = models.DateTimeField(null=True, blank=True)
    # After a grace replay, the refresh token the replay displaced. It stays
    # usable as a sibling of ``refresh_hash`` until either of them is used;
    # then the other one is retired.
    alt_refresh_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # Recently consumed / superseded refresh hashes: presenting one revokes the
    # device and alerts the member (the token was copied). Newest last, capped.
    retired_refresh_hashes = models.JSONField(default=list, blank=True)

    # Geo of the app sign-in (``run_post_auth_pipeline``), copied into every
    # web-view session the device opens so GeoRestrictionMiddleware sees it.
    login_geo = models.JSONField(default=dict, blank=True)
    login_geo_suspicious = models.BooleanField(default=False)

    auth_fingerprint = models.CharField(max_length=128)
    # Location check-in key (v4.1.0): hash of a second, narrow secret the app
    # keeps where a background task can read it while the phone is locked. It
    # only works on events/<id>/checkin/location/ (CheckinKeyAuthentication).
    checkin_key_hash = models.CharField(max_length=64, blank=True, db_index=True)
    verified_via = models.CharField(max_length=20, choices=VIA_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-last_seen_at', '-created_at']
        indexes = [models.Index(fields=['user', 'revoked_at'])]

    def __str__(self):
        return f'{self.get_platform_display()} device of {self.user_id}'

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    def revoke(self, reason):
        """
        Revoke this device and clear its push token (a lost phone stops showing
        pushes). Its web-view sessions end on their next request:
        ``MobileSessionGuardMiddleware`` checks the device behind every session
        that carries ``mobile_device_id``, so there are no session keys to track.
        """
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.revoked_reason = reason[:100]
            self.push_token = ''
            self.save(update_fields=['revoked_at', 'revoked_reason', 'push_token'])

    @classmethod
    def revoke_all_for(cls, user, reason, except_device=None):
        """Revoke every live device of ``user``. Returns the count."""
        qs = cls.objects.filter(user=user, revoked_at__isnull=True)
        if except_device is not None:
            qs = qs.exclude(pk=except_device.pk)
        return qs.update(revoked_at=timezone.now(), revoked_reason=reason[:100], push_token='')

    def still_allowed(self, user=None):
        """
        The per-request check. ``user`` defaults to ``self.user``.

        Returns (ok, reason). Never raises.
        """
        user = user or self.user
        now = timezone.now()
        if self.revoked_at is not None:
            return False, 'revoked'
        if self.created_at and now >= self.created_at + max_device_age():
            return False, 'reauth_required'
        if self.refresh_expires_at <= now:
            return False, 'expired'
        if not user.is_active:
            return False, 'inactive'
        if getattr(user, 'is_quarantined', False):
            return False, 'quarantined'
        if getattr(user, 'member_status', None) == 'Removed':
            return False, 'removed'
        if not secrets.compare_digest(self.auth_fingerprint, user.get_session_auth_hash()):
            return False, 'password_changed'
        if self.verified_via == 'password' and _second_factor_now_expected(user):
            return False, 'second_factor_required'
        return True, ''


class MobileAuthTicket(models.Model):
    PURPOSE_MFA = 'mfa'
    PURPOSE_PASSKEY_LOGIN = 'passkey_login'
    PURPOSE_PASSKEY_REAUTH = 'passkey_reauth'
    PURPOSE_WEB_HANDOFF = 'web_handoff'
    PURPOSE_WS = 'ws'
    PURPOSE_VOTE_GRANT = 'vote_grant'
    PURPOSE_CHOICES = [
        (PURPOSE_MFA, '2FA step after password'),
        (PURPOSE_PASSKEY_LOGIN, 'Passkey sign-in challenge'),
        (PURPOSE_PASSKEY_REAUTH, 'Passkey vote re-auth challenge'),
        (PURPOSE_WEB_HANDOFF, 'Web view session handoff'),
        (PURPOSE_WS, 'Websocket ticket'),
        (PURPOSE_VOTE_GRANT, 'One-shot vote re-auth grant'),
    ]
    #: Lifetimes, seconds.
    TTL = {
        PURPOSE_MFA: 300,
        PURPOSE_PASSKEY_LOGIN: 300,
        PURPOSE_PASSKEY_REAUTH: 300,
        PURPOSE_WEB_HANDOFF: 60,
        PURPOSE_WS: 30,
        PURPOSE_VOTE_GRANT: 120,
    }

    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    token_hash = models.CharField(max_length=64, unique=True)
    # Null for a passkey-login challenge (the user is unknown until the assertion).
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='mobile_auth_tickets')
    device = models.ForeignKey(
        MobileDevice, on_delete=models.CASCADE, null=True, blank=True, related_name='tickets')
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['purpose', 'expires_at'])]

    def __str__(self):
        return f'{self.purpose} ticket ({self.user_id})'

    @classmethod
    def issue(cls, purpose, user=None, device=None, payload=None):
        """Create a ticket; returns (raw_token, ticket). The raw token is shown once."""
        raw = new_secret()
        ticket = cls.objects.create(
            purpose=purpose,
            token_hash=hash_secret(raw),
            user=user,
            device=device,
            payload=payload or {},
            expires_at=timezone.now() + timedelta(seconds=cls.TTL[purpose]),
        )
        return raw, ticket

    @classmethod
    def consume(cls, purpose, raw, device=None):
        """
        Atomically mark a live ticket used and return it, or None.

        Single use: the UPDATE only matches an unused, unexpired row, so two
        concurrent redemptions can't both win. When ``device`` is given the
        ticket must have been issued to that device.
        """
        if not raw or not isinstance(raw, str):
            return None
        now = timezone.now()
        qs = cls.objects.filter(
            purpose=purpose, token_hash=hash_secret(raw), used_at__isnull=True,
            expires_at__gt=now)
        if device is not None:
            qs = qs.filter(device=device)
        ticket = qs.first()
        if ticket is None:
            return None
        claimed = cls.objects.filter(pk=ticket.pk, used_at__isnull=True).update(used_at=now)
        if not claimed:
            return None
        ticket.used_at = now
        return ticket


class MobilePushTicket(models.Model):
    """
    An Expo push ticket awaiting its receipt (mobile plan, Phase 1).

    Expo answers a send with a *ticket* per message; whether the message was
    actually handed to APNs/FCM is only known from the *receipt*, fetched
    later by ticket id (``tasks.check_expo_push_receipts``, every 15 minutes).
    A receipt of ``DeviceNotRegistered`` means the app was uninstalled, so the
    device's push token is cleared. ``push_token`` is the token the message was
    sent to: a token re-registered since then must not be cleared by a stale
    receipt for the old one.

    Rows are deleted once their receipt is processed, and after 24 h whatever
    happens (Expo keeps receipts for about a day).
    """
    ticket_id = models.CharField(max_length=100, unique=True)
    device = models.ForeignKey(
        MobileDevice, on_delete=models.CASCADE, related_name='push_tickets')
    push_token = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return f'push ticket {self.ticket_id}'
