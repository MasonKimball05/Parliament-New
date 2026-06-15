"""
Housekeeping tasks — prune stale records and expire time-limited entries.
All run on the nightly Celery Beat schedule (3:00–3:14 AM CST).
"""
from celery import shared_task
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@shared_task(name='tasks.cleanup_expired_sessions')
def cleanup_expired_sessions():
    """
    Remove expired UserSession records. Django's session engine handles its own
    expiry; this task cleans Parliament's UserSession tracking table.
    """
    try:
        from src.models import UserSession
        cutoff = timezone.now() - timezone.timedelta(days=30)
        deleted, _ = UserSession.objects.filter(last_activity__lt=cutoff).delete()
        if deleted:
            logger.info(f"[tasks] cleanup_expired_sessions: removed {deleted} stale UserSession records")
    except Exception as exc:
        logger.error(f"[tasks] cleanup_expired_sessions failed: {exc}")


@shared_task(name='tasks.prune_expired_login_lockouts')
def prune_expired_login_lockouts():
    """
    Delete LoginLockout records whose cache lockout has expired.

    LoginLockout rows are created for every IP/username lockout event so they
    show up in admin-v2. The cache entry that actually enforces the lockout
    expires automatically, but the DB row stays forever. This task prunes rows
    that are past their expires_at and were not manually cleared (cleared rows
    are worth keeping for audit history).
    """
    try:
        from src.models import LoginLockout
        cutoff = timezone.now()
        deleted, _ = LoginLockout.objects.filter(
            expires_at__lt=cutoff,
            is_cleared=False,
        ).delete()
        if deleted:
            logger.info(f"[tasks] prune_expired_login_lockouts: removed {deleted} expired LoginLockout records")
    except Exception as exc:
        logger.error(f"[tasks] prune_expired_login_lockouts failed: {exc}")


@shared_task(name='tasks.expire_stale_ip_blacklist_entries')
def expire_stale_ip_blacklist_entries():
    """
    Set is_active=False on IPBlacklist entries that have passed their expires_at.
    Entries with no expires_at are permanent and are left alone.
    """
    try:
        from src.models import IPBlacklist
        now = timezone.now()
        updated = IPBlacklist.objects.filter(
            is_active=True,
            expires_at__lt=now,
        ).exclude(expires_at=None).update(is_active=False)
        if updated:
            logger.info(f"[tasks] expire_stale_ip_blacklist_entries: deactivated {updated} expired IPBlacklist entries")
    except Exception as exc:
        logger.error(f"[tasks] expire_stale_ip_blacklist_entries failed: {exc}")


@shared_task(name='tasks.prune_stale_push_subscriptions')
def prune_stale_push_subscriptions():
    """
    Delete PushSubscription records unused for 90+ days.

    Subscriptions that return 410 Gone are deleted immediately on send. This
    task catches the rest: subscriptions that haven't been used in 90 days are
    almost certainly from browsers where the user revoked permission or cleared
    site data.
    Runs monthly (first of the month at 3:00 AM CST).
    """
    try:
        from src.models import PushSubscription
        cutoff = timezone.now() - timezone.timedelta(days=90)
        deleted, _ = PushSubscription.objects.filter(last_used_at__lt=cutoff).delete()
        if deleted:
            logger.info(f"[tasks] prune_stale_push_subscriptions: removed {deleted} stale PushSubscription records")
    except Exception as exc:
        logger.error(f"[tasks] prune_stale_push_subscriptions failed: {exc}")


@shared_task(name='tasks.cleanup_api_access_logs')
def cleanup_api_access_logs():
    """
    Delete APIAccessLog records older than 90 days.
    Runs monthly alongside push subscription pruning.
    """
    try:
        from src.models import APIAccessLog
        cutoff = timezone.now() - timezone.timedelta(days=90)
        deleted_count, _ = APIAccessLog.objects.filter(timestamp__lt=cutoff).delete()
        if deleted_count:
            logger.info(f"[tasks] cleanup_api_access_logs: deleted {deleted_count} records older than 90 days")
        return deleted_count
    except Exception as exc:
        logger.error(f"[tasks] cleanup_api_access_logs failed: {exc}")
        return 0


@shared_task(name='tasks.prune_expired_chat_permissions')
def prune_expired_chat_permissions():
    """
    Delete ChatChannelPermission rows whose expires_at has passed.

    Guest permissions can have an optional expiry date. When that date passes
    the permission is functionally dead (can_* checks filter it out), but the
    row remains. This task prunes those rows nightly so the guest list stays
    clean.
    """
    try:
        from src.models import ChatChannelPermission
        deleted, _ = ChatChannelPermission.objects.filter(
            expires_at__isnull=False,
            expires_at__lte=timezone.now(),
        ).delete()
        if deleted:
            logger.info(f"[tasks] prune_expired_chat_permissions: removed {deleted} expired permission(s)")
    except Exception as exc:
        logger.error(f"[tasks] prune_expired_chat_permissions failed: {exc}")
