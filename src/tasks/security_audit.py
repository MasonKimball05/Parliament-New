"""
Scheduled security-audit tasks. Currently just the weekly weak-password
check; split into its own module (rather than added to notifications.py or
cleanup.py) because it's neither a user-facing notification nor a deletion —
it's an audit that files security alerts, closer in spirit to the checks in
tasks.send_daily_digest but on its own weekly cadence rather than nightly.
"""
from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(name='tasks.check_weak_passwords')
def check_weak_passwords():
    """
    Weekly weak-password audit — see src/weak_password_audit.py for the full
    design (candidate list, performance bound, why active-only). Runs via
    Celery Beat ('Weekly weak-password audit' in setup_celery_schedules.py).

    Only sends an email when something is actually found — unlike
    send_daily_digest, which always sends so its own absence is a signal,
    this task runs weekly specifically to check on something that's usually
    fine; an "all clear" email every week would just train officers to
    ignore it. A LoginAlert row is still created for every match regardless
    of email (see audit_active_users_for_weak_passwords), so nothing here
    depends on the email actually being delivered — it's a convenience nudge
    on top of the alert that's always there in admin-v2.
    """
    try:
        from src.weak_password_audit import audit_active_users_for_weak_passwords
        summary = audit_active_users_for_weak_passwords()
        logger.info(f"[tasks] check_weak_passwords: {summary}")

        if summary['flagged']:
            from src.tasks.email import send_security_alert_task
            # force_send=True because send_security_alert only emails
            # automatically for severity='critical'; this is 'high' (matches
            # the per-user LoginAlert severity) but a weekly finding like
            # this is exactly the kind of thing that should reach an officer
            # promptly rather than wait to be noticed on the dashboard.
            send_security_alert_task.delay(
                event_type='weekly_weak_password_audit',
                severity='high',
                details=(
                    f"The weekly password audit flagged {summary['flagged']} "
                    f"account(s) with a known-weak password. See admin-v2 → "
                    f"Security Alerts for details (filter: Weak/Compromised "
                    f"Password)."
                ),
                force_send=True,
            )
    except Exception as exc:
        logger.error(f"[tasks] check_weak_passwords failed: {exc}")
