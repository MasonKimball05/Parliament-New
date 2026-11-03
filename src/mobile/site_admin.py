"""
The app's Site admin gate (v4.4.3): the website's admin-v2 gate, per device.

The website's Admin v2 needs two things: the member's id in
``ADMIN_V2_USER_IDS`` (``admin_v2.ALLOWED_USER_IDS``), and a separate step-up
(the member's password AND ``ADMIN_V2_SECRET_KEY``, or a passkey) that opens a
7-day admin session. The app keeps both halves:

* **Allowlist.** Every Site admin endpoint answers 404 to anyone not on it,
  before anything else is looked at, so the area can't be discovered, and
  ``me/`` only says ``site_admin: true`` to allowlisted members.
* **Step-up.** ``admin/unlock/`` runs the website's own checks
  (``admin_v2.admin_v2_step_up``): the same per-member rate limit
  (``admin_v2_attempts_<pk>``, 5 tries then 15 minutes, shared with the
  website), the same ``security_violation`` / ``admin_v2_access`` rows, the
  secret compared in constant time. No passkey: the app can't add one
  without a native module.
* **Per device, in the database.** Success sets
  ``MobileDevice.admin_unlocked_until`` on the install that asked, 12 hours
  ahead (shorter than the website's 7 days: a phone is easier to lose). The
  cache can be per-process, so it is not used for this. Revoking or signing
  out the install ends it (``MobileDevice.revoke`` clears it, and a revoked
  token can't authenticate anyway); ``admin/lock/`` ends it on demand.
* Locked → 403 ``admin_locked``; the app goes back to its unlock screen.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework import exceptions
from rest_framework.views import APIView

from src.mobile.api import MobileAPIView, check_web_page

UNLOCK_HOURS = 12
LOCKED_CODE = 'admin_locked'


def is_site_admin(user):
    """On the admin-v2 allowlist (read at call time, so tests' patches apply)."""
    from src.view.admin_v2 import is_admin_v2_allowlisted
    return is_admin_v2_allowlisted(user)


def unlocked_until(device):
    """The device's unlock expiry if it is still in the future, else None."""
    until = getattr(device, 'admin_unlocked_until', None)
    if until and until > timezone.now():
        return until
    return None


def unlock_device(device):
    until = timezone.now() + timedelta(hours=UNLOCK_HOURS)
    device.admin_unlocked_until = until
    device.save(update_fields=['admin_unlocked_until'])
    return until


def lock_device(device):
    if device.admin_unlocked_until is not None:
        device.admin_unlocked_until = None
        device.save(update_fields=['admin_unlocked_until'])


class SiteAdminAPIView(MobileAPIView):
    """
    Base for every ``admin/`` endpoint: Bearer auth (``MobileAPIView``), then
    the allowlist (404), then the device's unlock (403 ``admin_locked``)
    unless ``requires_unlock = False`` (status, unlock, lock). ``needs_is_admin``
    mirrors the website pages that also check ``user.is_admin`` (lockdown,
    lockouts, audit log): 403, as there.
    """

    requires_unlock = True
    needs_is_admin = False
    # The admin-v2 pages have no @require_page_enabled.
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        # APIView.initial: authentication, permissions, throttles. The web page
        # check (MobileAPIView.initial) runs after the allowlist, so a member
        # who isn't on it gets the same 404 whatever their restrictions.
        APIView.initial(self, request, *args, **kwargs)
        if not is_site_admin(request.user):
            raise exceptions.NotFound()
        if self.requires_unlock and unlocked_until(request.auth) is None:
            raise exceptions.PermissionDenied('Unlock Site admin to continue.', code=LOCKED_CODE)
        if self.needs_is_admin and not request.user.is_admin:
            raise exceptions.PermissionDenied('Admin access required.', code='admin_required')
        if self.web_page:
            check_web_page(request.user, self.web_page, toggle=self.web_page_toggle)
