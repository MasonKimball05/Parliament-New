"""
Notification utilities for Parliament system
Handles email and in-app notifications for announcements, events, and other updates
"""
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils.html import strip_tags
from django.urls import reverse
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
    # Get all ACTIVE users who should receive this announcement
    # NOTE: We only send emails to ACTIVE members to avoid spam to alumni/inactive users
    if announcement.visible_to:
        # Filter by member types if visibility is restricted
        # "Member" includes Chair and Officer types
        member_types = list(announcement.visible_to)
        if 'Member' in member_types:
            member_types.extend(['Chair', 'Officer'])

        users = ParliamentUser.objects.filter(
            member_status='Active',
            member_type__in=member_types,
            email__isnull=False
        ).exclude(email='')
    else:
        # Send to all active users with emails
        users = ParliamentUser.objects.filter(
            member_status='Active',
            email__isnull=False
        ).exclude(email='')

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
            logger.info(f"Sent announcement email to {user.email}")

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
