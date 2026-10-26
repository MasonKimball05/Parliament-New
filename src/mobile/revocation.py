"""
When the app's devices are signed out (mobile plan, Phase 1).

Checked live on every request, so nothing here has to do them:

* password change by any path — ``auth_fingerprint`` stops matching
  ``get_session_auth_hash()`` (``MobileDevice.still_allowed``);
* quarantine, deactivation, ``member_status='Removed'`` — read from the user
  row on every request.

Revoked explicitly, because they don't touch the password:

* a confirmed TOTP device, a static (backup-code) device, or a passkey is
  deleted — by the member, an admin, or 2FA self-service recovery (which
  deletes the devices, so these receivers cover it). The device signed in with
  a factor that no longer exists, so it has to sign in again;
* "log out all other sessions" (``UserSession.revoke_other_sessions``) — see
  ``revoke_for_sign_out_everywhere`` below.

Each device's web-view sessions end on their next request:
``MobileSessionGuardMiddleware`` checks the device behind every session that
carries ``mobile_device_id``.

Receivers are connected on import; ``src/signals.py`` imports this module.

Note: regenerating backup codes deletes the old StaticDevice, so it signs the
app out too. That's on purpose — old codes are usually regenerated because they
may have been seen.
"""
import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models.mobile import MobileDevice
from src.models.webauthn import WebAuthnCredential

logger = logging.getLogger('admin_actions')


def revoke_user_devices(user_id, reason, except_device_id=None):
    qs = MobileDevice.objects.filter(user_id=user_id, revoked_at__isnull=True)
    if except_device_id is not None:
        qs = qs.exclude(pk=except_device_id)
    from django.utils import timezone
    count = qs.update(revoked_at=timezone.now(), revoked_reason=reason[:100], push_token='')
    if count:
        logger.info('MOBILE DEVICES REVOKED: %s device(s) of user %s (%s)', count, user_id, reason)
    return count


def revoke_for_sign_out_everywhere(user, keep_session_key=None):
    """
    "Log out all other sessions": also sign out every app install, except the
    one whose web view is the session being kept (signing yourself out of the
    app from inside the app would be surprising, and that device is the one
    the member is holding).
    """
    keep_device_id = None
    if keep_session_key:
        try:
            from importlib import import_module
            from django.conf import settings
            store = import_module(settings.SESSION_ENGINE).SessionStore(session_key=keep_session_key)
            keep_device_id = store.get('mobile_device_id')
        except Exception:
            keep_device_id = None
    return revoke_user_devices(user.pk, 'signed out everywhere', except_device_id=keep_device_id)


@receiver(post_delete, sender=TOTPDevice, dispatch_uid='mobile_revoke_on_totp_delete')
def _totp_deleted(sender, instance, **kwargs):
    # An unconfirmed device is a half-finished setup that never protected
    # anything; deleting one isn't a security event.
    if instance.confirmed:
        revoke_user_devices(instance.user_id, 'authenticator removed')


@receiver(post_delete, sender=StaticDevice, dispatch_uid='mobile_revoke_on_static_delete')
def _static_deleted(sender, instance, **kwargs):
    if instance.confirmed:
        revoke_user_devices(instance.user_id, 'backup codes removed')


@receiver(post_delete, sender=WebAuthnCredential, dispatch_uid='mobile_revoke_on_passkey_delete')
def _passkey_deleted(sender, instance, **kwargs):
    revoke_user_devices(instance.user_id, 'passkey removed')
