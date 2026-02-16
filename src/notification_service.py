"""
Notification service helpers for creating in-app notifications.

Usage:
    from src.notification_service import notify_all_active_members, notify_users, create_notification

    # Notify all active members (e.g., new announcement)
    notify_all_active_members(
        'announcement', 'New Announcement: Title Here',
        message='Preview text...', link='/announcements/',
        source_type='Announcement', source_id=1,
        exclude_user=request.user
    )

    # Notify specific users (e.g., vote ended)
    notify_users(
        user_queryset, 'vote_ended', 'Vote Ended: Title',
        link='/passed-legislation/5/',
        source_type='Legislation', source_id=5
    )
"""
import logging
from django.utils import timezone
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _invalidate_caches_for_users(user_pks):
    """Invalidate notification count caches for a list of user PKs."""
    keys = [f'notif_count_{pk}' for pk in user_pks]
    cache.delete_many(keys)

# Maps notification_type -> UserPreferences field name
NOTIFICATION_PREF_MAP = {
    'announcement': 'notify_announcements',
    'legislation_new': 'notify_legislation',
    'vote_ended': 'notify_legislation',
    'event_new': 'notify_events',
    # Slating notifications
    'slating_open': 'notify_slating',
    'slating_voting': 'notify_slating',
    'slating_results': 'notify_slating',
}


def _user_wants_notification(user, notification_type):
    """Check if a user has opted in to this notification type."""
    pref_field = NOTIFICATION_PREF_MAP.get(notification_type)
    if not pref_field:
        return True  # Unknown type, default to sending

    try:
        return getattr(user.preferences, pref_field, True)
    except Exception:
        return True  # No preferences record, default to sending


def create_notification(recipient, notification_type, title, message='', link='', source_type='', source_id=None):
    """Create a single notification for one user, respecting their preferences."""
    if not _user_wants_notification(recipient, notification_type):
        return None

    from src.models import Notification
    notification = Notification.objects.create(
        recipient=recipient,
        notification_type=notification_type,
        title=title,
        message=message,
        link=link,
        source_type=source_type,
        source_id=source_id,
    )
    cache.delete(f'notif_count_{recipient.pk}')
    return notification


def notify_all_active_members(notification_type, title, message='', link='', source_type='', source_id=None, exclude_user=None):
    """
    Create a notification for all active members, respecting individual preferences.
    Optionally excludes a user (e.g., the person who triggered the action).
    """
    from src.models import ParliamentUser, Notification

    members = ParliamentUser.objects.filter(member_status='Active')
    if exclude_user:
        members = members.exclude(pk=exclude_user.pk)

    # Prefetch preferences to avoid N+1
    members = members.select_related('preferences')

    notifications = []
    for member in members:
        if _user_wants_notification(member, notification_type):
            notifications.append(Notification(
                recipient=member,
                notification_type=notification_type,
                title=title,
                message=message,
                link=link,
                source_type=source_type,
                source_id=source_id,
            ))

    if notifications:
        Notification.objects.bulk_create(notifications)
        # Invalidate caches for all recipients
        recipient_pks = [n.recipient_id for n in notifications]
        _invalidate_caches_for_users(recipient_pks)
        logger.info(f"Created {len(notifications)} '{notification_type}' notifications: {title}")

    return len(notifications)


def notify_users(users, notification_type, title, message='', link='', source_type='', source_id=None):
    """
    Create notifications for a specific set of users, respecting individual preferences.
    `users` can be a queryset or iterable of ParliamentUser instances.
    """
    from src.models import Notification

    # Ensure we have preferences loaded
    if hasattr(users, 'select_related'):
        users = users.select_related('preferences')

    notifications = []
    for user in users:
        if _user_wants_notification(user, notification_type):
            notifications.append(Notification(
                recipient=user,
                notification_type=notification_type,
                title=title,
                message=message,
                link=link,
                source_type=source_type,
                source_id=source_id,
            ))

    if notifications:
        Notification.objects.bulk_create(notifications)
        # Invalidate caches for all recipients
        recipient_pks = [n.recipient_id for n in notifications]
        _invalidate_caches_for_users(recipient_pks)
        logger.info(f"Created {len(notifications)} '{notification_type}' notifications for targeted users: {title}")

    return len(notifications)
