"""
Async email wrappers — thin tasks so email sends never block a gunicorn worker.
"""
from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_announcement_email')
def send_announcement_email(self, announcement_id, initiated_by_id=None):
    """
    Send announcement notification emails asynchronously.
    Called from manage_announcements.py instead of calling send_announcement_notification() directly.
    """
    try:
        from src.models import Announcement, ParliamentUser
        from src.notifications import send_announcement_notification
        announcement = Announcement.objects.get(pk=announcement_id)
        initiated_by = ParliamentUser.objects.filter(pk=initiated_by_id).first() if initiated_by_id else None
        send_announcement_notification(announcement, initiated_by=initiated_by)
    except Announcement.DoesNotExist:
        logger.warning(f"[tasks] Announcement {announcement_id} no longer exists — skipping email")
    except Exception as exc:
        logger.error(f"[tasks] send_announcement_email failed for id={announcement_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_security_alert_task')
def send_security_alert_task(self, event_type, severity, details, ip_address=None, user_id=None, force_send=False):
    """
    Send a security alert email asynchronously.
    Replaces direct calls to security_notifications.send_security_alert() in hot paths
    (middleware, login view) so attacks don't add email latency to the blocked request.
    """
    try:
        from src.security_notifications import send_security_alert
        from src.models import ParliamentUser
        user = ParliamentUser.objects.filter(pk=user_id).first() if user_id else None
        send_security_alert(
            event_type=event_type,
            severity=severity,
            details=details,
            ip_address=ip_address,
            user=user,
            force_send=force_send,
        )
    except Exception as exc:
        logger.error(f"[tasks] send_security_alert_task failed ({event_type}): {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_email')
def send_email(self, subject, body, from_email, recipient_list, fail_silently=False):
    """
    Generic async wrapper for one-off send_mail calls in views.
    Accepts plain-text body only. Use send_announcement_email for HTML emails.
    """
    try:
        from django.core.mail import send_mail as _send_mail
        _send_mail(subject, body, from_email, recipient_list, fail_silently=fail_silently)
    except Exception as exc:
        logger.error(f"[tasks] send_email failed (subject='{subject}'): {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=120, name='tasks.send_pledge_welcome_task')
def send_pledge_welcome_task(self, user_id, temp_password):
    """Send pledge welcome email asynchronously after account creation."""
    try:
        from src.models import ParliamentUser
        from src.notifications import send_pledge_welcome_email
        user = ParliamentUser.objects.get(pk=user_id)
        send_pledge_welcome_email(user, temp_password)
    except Exception as exc:
        logger.error(f"[tasks] send_pledge_welcome_task failed for user_id={user_id}: {exc}")
        raise self.retry(exc=exc)
