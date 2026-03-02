"""
Notification utilities for Parliament system
Handles email and in-app notifications for announcements, events, and other updates
"""
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
from django.urls import reverse
from django.db.models import Q
from src.models import ParliamentUser, Announcement, UserAnnouncementView
import logging

logger = logging.getLogger(__name__)


def get_site_url():
    """Get the site URL from settings"""
    return getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')


def send_announcement_notification(announcement):
    """
    Send email notification to all ACTIVE users who should see this announcement

    IMPORTANT: Only ACTIVE members receive email notifications.
    Inactive/Alumni members can still see announcements in-app, but won't get emails.

    Args:
        announcement: Announcement instance that was just created/published
    """
    # Log the announcement visibility settings for debugging
    logger.info(f"[ANNOUNCEMENT EMAIL] Starting email send for announcement '{announcement.title}' (ID: {announcement.id})")
    logger.info(f"[ANNOUNCEMENT EMAIL] Raw visible_to value: {announcement.visible_to} (type: {type(announcement.visible_to)})")

    # Get all ACTIVE users who should receive this announcement
    # NOTE: We only send emails to ACTIVE members to avoid spam to alumni/inactive users
    if announcement.visible_to:
        # Filter by member types if visibility is restricted
        # "Member" includes Chair and Officer types
        member_types = list(announcement.visible_to)
        logger.info(f"[ANNOUNCEMENT EMAIL] Initial member_types from visible_to: {member_types}")

        if 'Member' in member_types:
            member_types.extend(['Chair', 'Officer'])
            logger.info(f"[ANNOUNCEMENT EMAIL] Expanded member_types (added Chair/Officer): {member_types}")

        # First, get ALL users that match visibility (for logging)
        all_targeted_users = ParliamentUser.objects.filter(
            member_status='Active',
            member_type__in=member_types
        )

        # Then filter to only those with valid emails who want notifications
        # Note: email_announcements is on UserPreferences model
        # Include users who have email_announcements=True OR don't have preferences set (default is to send)
        users = all_targeted_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__email_announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        # Log the breakdown
        total_targeted = all_targeted_users.count()
        users_with_email = users.count()
        users_no_email = total_targeted - users_with_email

        logger.info(f"[ANNOUNCEMENT EMAIL] Targeting member_types: {member_types}")
        logger.info(f"[ANNOUNCEMENT EMAIL] Total users matching visibility: {total_targeted}")
        logger.info(f"[ANNOUNCEMENT EMAIL] Users with valid email (will receive): {users_with_email}")
        logger.info(f"[ANNOUNCEMENT EMAIL] Users without email (skipped): {users_no_email}")
    else:
        # Send to all active users with emails
        logger.info(f"[ANNOUNCEMENT EMAIL] No visibility restriction - sending to ALL active members")

        # First, get ALL active users (for logging)
        all_active_users = ParliamentUser.objects.filter(member_status='Active')

        # Then filter to only those with valid emails who want notifications
        # Note: email_announcements is on UserPreferences model
        # Include users who have email_announcements=True OR don't have preferences set (default is to send)
        users = all_active_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__email_announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        # Log the breakdown
        total_active = all_active_users.count()
        users_with_email = users.count()
        users_no_email = total_active - users_with_email

        logger.info(f"[ANNOUNCEMENT EMAIL] Total active users: {total_active}")
        logger.info(f"[ANNOUNCEMENT EMAIL] Users with valid email (will receive): {users_with_email}")
        logger.info(f"[ANNOUNCEMENT EMAIL] Users without email (skipped): {users_no_email}")

    # Log user counts by member type for verification
    user_counts = {}
    for user in users:
        user_counts[user.member_type] = user_counts.get(user.member_type, 0) + 1
    logger.info(f"[ANNOUNCEMENT EMAIL] Final recipients by member_type: {user_counts}")

    if not users.exists():
        logger.info(f"No users with emails to notify for announcement: {announcement.title}")
        return 0

    # Prepare email content
    subject = f"New Announcement: {announcement.title}"
    site_url = get_site_url()

    # Send emails - each user gets a unique email with their tracking pixel
    sent_count = 0
    failed_count = 0

    for user in users:
        # Safety check: skip users without valid email
        if not user.email or not user.email.strip():
            logger.warning(f"[ANNOUNCEMENT EMAIL] Skipping user {user.user_id} ({user.member_type}) - no valid email")
            continue

        try:
            # Generate user-specific tracking URL
            tracking_url = f"{site_url}/track/announcement/{announcement.id}/user/{user.user_id}/"

            # Create HTML email with tracking pixel
            html_message = render_to_string('emails/announcement_notification.html', {
                'announcement': announcement,
                'site_url': site_url,
                'tracking_url': tracking_url,
                'user': user,
            })

            # Create plain text version
            plain_message = strip_tags(html_message)

            msg = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email]
            )
            msg.attach_alternative(html_message, "text/html")
            msg.send()

            sent_count += 1
            logger.info(f"[ANNOUNCEMENT EMAIL] Sent to {user.email} ({user.member_type})")

        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to send announcement email to {user.email}: {str(e)}")

    logger.info(f"Announcement notification complete. Sent: {sent_count}, Failed: {failed_count}")
    return sent_count


def get_unread_announcements(user):
    """
    Get announcements that the user hasn't dismissed yet
    Note: In-app notifications shown to all members, but emails only sent to active members

    Args:
        user: ParliamentUser instance

    Returns:
        List of Announcement objects
    """
    from django.db.models import Q
    from django.utils import timezone

    # Get announcements the user has dismissed
    dismissed_ids = UserAnnouncementView.objects.filter(
        user=user,
        dismissed=True
    ).values_list('announcement_id', flat=True)

    # Get active, published announcements from the last 7 days
    seven_days_ago = timezone.now() - timezone.timedelta(days=7)
    now = timezone.now()

    announcements = Announcement.objects.filter(
        is_active=True,
        posted_at__gte=seven_days_ago
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).exclude(
        id__in=dismissed_ids
    ).order_by('-posted_at')

    # Filter by visibility
    visible_announcements = [a for a in announcements if a.is_visible_to_user(user)]

    return visible_announcements


def mark_announcement_dismissed(user, announcement_id):
    """
    Mark an announcement as dismissed by the user

    Args:
        user: ParliamentUser instance
        announcement_id: ID of the announcement to dismiss
    """
    try:
        view, created = UserAnnouncementView.objects.get_or_create(
            user=user,
            announcement_id=announcement_id
        )
        view.dismissed = True
        view.save()
        logger.info(f"User {user.username} dismissed announcement {announcement_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to mark announcement {announcement_id} as dismissed for {user.username}: {str(e)}")
        return False
