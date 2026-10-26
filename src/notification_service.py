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


_PUSH_PREF_MAP = {
    'announcement': 'push_announcements',
    'legislation_new': 'push_legislation',
    'vote_ended': 'push_legislation',
    'event_new': 'push_events',
    'slating_open': 'push_slating',
    'slating_voting': 'push_slating',
    'slating_results': 'push_slating',
    # Mobile plan, Phase 1 — types that used to create an in-app row with no
    # push at all. Each has its own prefs['push'] key, defaulting on.
    'excuse_reviewed': 'push_excuses',
    'security': 'push_security',
    'chat_mention': 'push_mentions',
    # 'role_kb_available' is deliberately unmapped: it fires once per role
    # transfer, and an unmapped type always pushes.
}


#: Lock-screen text for types whose message is private (login country, passkey
#: and device names, officer review notes): the push says only that something
#: happened; the details stay in the in-app notification.
_GENERIC_PUSH_BODY = {
    'security': 'New security alert on your account',
    'excuse_reviewed': 'Your excuse was reviewed',
}


def _push_body(notif, body):
    if body is not None:
        return body
    generic = _GENERIC_PUSH_BODY.get(notif.notification_type)
    if generic is not None:
        return generic
    return notif.message[:120] if notif.message else ''


def _dispatch_push_notifications(notifications, pref_attr=None, title=None, body=None):
    """
    Fire a send_push_notification Celery task (web + mobile) for each
    notification recipient, respecting per-type push preferences. Recipients
    with no browser subscription or app device are skipped by the tasks.

    ``pref_attr`` overrides the preference looked up from the type (scheduled
    reminders are stored as 'announcement' rows but have their own toggle);
    ``title`` / ``body`` override what is shown on the lock screen (a mention
    in a private channel must not put the message there).
    """
    try:
        from src.tasks import send_push_notification
        for notif in notifications:
            # Check per-type push preference — default True if prefs not loaded
            attr = pref_attr or _PUSH_PREF_MAP.get(notif.notification_type)
            if attr:
                prefs = getattr(notif.recipient, 'preferences', None)
                if prefs is not None and not getattr(prefs, attr, True):
                    continue
            send_push_notification.delay(
                user_id=notif.recipient_id,
                title=title if title is not None else notif.title,
                body=_push_body(notif, body),
                url=notif.link or '/home/',
                tag=f'{notif.notification_type}-{notif.source_id or ""}',
            )
    except Exception as exc:
        logger.warning(f'[push] dispatch failed: {exc}')

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
    # Excuse review outcome (v3.33.0) — added 09-17-26, defaults to on via
    # notify_excuses' own default (see UserPreferences._default_user_prefs).
    'excuse_reviewed': 'notify_excuses',
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


def create_notification(recipient, notification_type, title, message='', link='', source_type='', source_id=None,
                        push=True, push_title=None, push_body=None):
    """
    Create a single notification for one user, respecting their preferences,
    and push it (web + mobile) unless ``push`` is False or the member turned
    that type's push off. ``push_title`` / ``push_body`` replace the
    lock-screen text when the row's own text is too sensitive to show there.
    """
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
    if push:
        _dispatch_push_notifications([notification], title=push_title, body=push_body)
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
        _dispatch_push_notifications(notifications)
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
        _dispatch_push_notifications(notifications)
        logger.info(f"Created {len(notifications)} '{notification_type}' notifications for targeted users: {title}")

    return len(notifications)
