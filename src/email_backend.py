"""
Feature-flag-gated email backend.

WHY A BACKEND AND NOT FIFTEEN CALL SITES
-----------------------------------------
`email_notifications` (FeatureFlag) is meant to be a chapter-wide kill switch
for outbound email — the kind of thing you want during testing against a real
member list, or if a bug starts sending duplicates and you need to stop the
bleeding before you can deploy a fix. It has existed as a toggle in the admin
since the feature-flag system was built and has gated nothing: 15 files call
`django.core.mail.send_mail` / `EmailMultiAlternatives` / `send_mass_mail`
directly, and wiring the flag by editing each one is exactly the enumeration
this codebase has been burned by before (v3.18.2's rule: enumerate what CAN
send the thing, not each call site you happen to remember — and the next
caller nobody remembers to patch is silently unguarded).

Every one of those call sites already funnels through ONE place: Django
resolves `settings.EMAIL_BACKEND` once per `get_connection()` call, and
`send_messages()` is the method that actually talks to the outside world.
Gate that, and a call site written next year is covered by construction —
nobody has to remember it exists.

WHY IT FAILS OPEN
------------------
If the flag lookup itself fails (cache backend down, `FeatureFlag` table
missing during a fresh install before `seed_feature_flags` has run, a
management command invoked before migrations), this backend still sends the
mail. Email going out during an infrastructure hiccup is a far smaller
problem than every notification in the app going silently missing because a
lookup that has nothing to do with email happened to fail. This mirrors
`FeatureFlag.is_feature_enabled`'s own fail-open default for every flag not
in `DISABLED_BY_DEFAULT`.

NOT COVERED, ON PURPOSE
------------------------
Security-relevant mail — two-factor codes, password reset, account-lockout
notices — is not run through `is_feature_enabled` at all; see the allowlist
below. Turning off "email notifications" (announcements, digests, event
reminders) must not also turn off the emails that keep an account secure.
"""
import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

#: Subject-line substrings that bypass the flag entirely. Checked against
#: the RENDERED subject because that's the one thing every EmailMessage
#: subtype (EmailMessage, EmailMultiAlternatives) exposes uniformly — there
#: is no shared "message category" field to key off instead.
#:
#: Verified against the actual subjects each site sends, not guessed:
#:   '[SECURITY ALERT]'   src/security_notifications.py:55  (login anomalies)
#:   '[WATCH FLAG]'       src/security_notifications.py:521,585 (monitored-user alerts)
#:   '2FA'                src/view/two_factor_recovery.py:105,180
#:   'Confirm your new email address'  src/view/set_email.py:64
#:   'Your Parliament Password Has Been Reset'  src/view/admin_v2.py:2055
#:   'PREFLIGHT FAILED'   src/management/commands/preflight.py:659 (ops watchdog)
#:   'digest watchdog'    src/management/commands/check_digest_freshness.py:80
#:
#: Everything else — announcements, Kai report notices, digests, bug reports,
#: the contact form — is a member-facing notification and stays gated.
#:
#: ⚠️ Kept short and specific on purpose. This is a narrow security/ops
#: carve-out, not a general escape hatch — anything added here skips the kill
#: switch silently, which is the opposite of what the flag promises. If a new
#: message needs to bypass `email_notifications`, add its real subject here
#: with a comment, don't widen an existing marker to catch it.
_ALWAYS_SEND_SUBJECT_MARKERS = (
    '[SECURITY ALERT]',
    '[WATCH FLAG]',
    '2FA',
    'Confirm your new email address',
    'Your Parliament Password Has Been Reset',
    'PREFLIGHT FAILED',
    'digest watchdog',
)


def _bypasses_flag(message):
    subject = getattr(message, 'subject', '') or ''
    return any(marker in subject for marker in _ALWAYS_SEND_SUBJECT_MARKERS)


class FeatureFlagGatedEmailBackend(BaseEmailBackend):
    """
    Delegates every real send to `settings.REAL_EMAIL_BACKEND` unless the
    `email_notifications` feature flag is off, in which case non-security
    messages are silently dropped (logged, not queued, not retried) and the
    call site sees a normal "N sent" return value — nothing raises, because
    nothing calling `send_mail` expects a flag-driven failure mode.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        real_backend_path = getattr(
            settings, 'REAL_EMAIL_BACKEND',
            'django.core.mail.backends.smtp.EmailBackend',
        )
        backend_class = import_string(real_backend_path)
        self._real_backend = backend_class(fail_silently=fail_silently, **kwargs)

    def open(self):
        return self._real_backend.open()

    def close(self):
        return self._real_backend.close()

    def _flag_enabled(self):
        try:
            from src.models_feature_flags import FeatureFlag
            return FeatureFlag.is_feature_enabled('email_notifications')
        except Exception:
            # See module docstring: fail OPEN. A broken flag lookup must not
            # silently disable every email the app sends.
            logger.warning(
                'email_notifications flag lookup failed — sending anyway '
                '(fail-open).', exc_info=True,
            )
            return True

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if self._flag_enabled():
            return self._real_backend.send_messages(email_messages)

        to_send = [m for m in email_messages if _bypasses_flag(m)]
        dropped = [m for m in email_messages if m not in to_send]

        if dropped:
            recipients = sorted({addr for m in dropped for addr in (m.to or [])})
            logger.info(
                'email_notifications is disabled — dropped %d message(s), '
                'recipients: %s',
                len(dropped), ', '.join(recipients) or '(none)',
            )

        sent = self._real_backend.send_messages(to_send) if to_send else 0
        # Callers only ever check this for truthiness / a count to log — none
        # of the 15 call sites branch on the exact number — so reporting the
        # full count (sent + silently dropped) keeps "how many did I hand the
        # backend" and "how many did send_messages claim" consistent, rather
        # than making the flag look like a delivery failure.
        return (sent or 0) + len(dropped)
