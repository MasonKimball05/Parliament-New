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
from django.utils import timezone
from src.models import ParliamentUser, Announcement, UserAnnouncementView, AnnouncementEmailLog, AnnouncementEmailRecipient
import logging

logger = logging.getLogger(__name__)


def get_site_url():
    """Get the site URL from settings"""
    return getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')


def send_announcement_notification(announcement, initiated_by=None):
    """
    Send email notification to all ACTIVE users who should see this announcement.
    Creates detailed logs of all send attempts.

    IMPORTANT: Only ACTIVE members receive email notifications.
    Inactive/Alumni members can still see announcements in-app, but won't get emails.

    Args:
        announcement: Announcement instance that was just created/published
        initiated_by: ParliamentUser who initiated the send (optional)

    Returns:
        int: Number of emails successfully sent
    """
    # Console log buffer
    console = []
    def log(msg):
        console.append(f"[{timezone.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}")
        logger.info(f"[ANNOUNCEMENT EMAIL] {msg}")

    # Create the email log entry FIRST to ensure we always have a record
    email_log = None
    sent_count = 0
    failed_count = 0

    try:
        email_log = AnnouncementEmailLog.objects.create(
            announcement=announcement,
            initiated_by=initiated_by,
            visible_to_raw=announcement.visible_to,
            expanded_member_types=None,
            total_active_users=0,
            users_matching_visibility=0,
            users_with_valid_email=0,
            status='started'
        )
        log(f"Created email log entry ID: {email_log.id}")
    except Exception as e:
        logger.error(f"[ANNOUNCEMENT EMAIL] Failed to create email log: {e}")
        raise

    try:
        log(f"=" * 60)
        log(f"STARTING EMAIL SEND")
        log(f"=" * 60)
        log(f"Announcement: '{announcement.title}' (ID: {announcement.id})")
        log(f"Initiated by: {getattr(initiated_by, 'name', 'System') if initiated_by else 'System'}")
        log(f"")
        log(f"VISIBILITY SETTINGS:")
        log(f"  Raw visible_to: {announcement.visible_to}")
        log(f"  Type: {type(announcement.visible_to)}")

        # Get ALL users for comprehensive logging
        all_users = ParliamentUser.objects.all()
        all_active_users = ParliamentUser.objects.filter(member_status='Active')

        log(f"")
        log(f"USER COUNTS:")
        log(f"  Total users in system: {all_users.count()}")
        log(f"  Active users: {all_active_users.count()}")

        # Determine member types to target
        if announcement.visible_to:
            member_types = list(announcement.visible_to)
            log(f"")
            log(f"VISIBILITY EXPANSION:")
            log(f"  Original types: {announcement.visible_to}")
            if 'Member' in member_types:
                member_types.extend(['Chair', 'Officer'])
                log(f"  'Member' found - expanding to include Chair, Officer")
            log(f"  Final targeted types: {member_types}")
            targeted_users = all_active_users.filter(member_type__in=member_types)
        else:
            member_types = None  # All types
            log(f"")
            log(f"VISIBILITY: No restrictions - targeting ALL active users")
            targeted_users = all_active_users

        log(f"  Users matching visibility: {targeted_users.count()}")

        # Log member type breakdown
        log(f"")
        log(f"ACTIVE USERS BY TYPE:")
        for mt in ['Member', 'Officer', 'Chair', 'Pledge', 'Advisor', 'Alumni']:
            count = all_active_users.filter(member_type=mt).count()
            targeted = targeted_users.filter(member_type=mt).count() if member_types is None or mt in member_types else 0
            status_str = "TARGETED" if (member_types is None or mt in member_types) else "excluded"
            log(f"  {mt}: {count} active, {targeted} {status_str}")

        # Filter to users with valid emails who want notifications
        users_to_email = targeted_users.filter(
            email__isnull=False
        ).filter(
            Q(preferences__email_announcements=True) | Q(preferences__isnull=True)
        ).exclude(email='')

        log(f"")
        log(f"EMAIL FILTERING:")
        log(f"  Users with valid email who want notifications: {users_to_email.count()}")

        # Update the email log with computed values
        email_log.expanded_member_types = member_types
        email_log.total_active_users = all_active_users.count()
        email_log.users_matching_visibility = targeted_users.count()
        email_log.users_with_valid_email = users_to_email.count()
        email_log.save()

        # Log ALL users with their status
        log(f"")
        log(f"PROCESSING ALL USERS:")
        recipients_to_create = []
        status_counts = {'pending': 0, 'skipped_inactive': 0, 'skipped_visibility': 0, 'skipped_no_email': 0, 'skipped_disabled': 0}

        for user in all_users:
            # Determine status for this user
            if user.member_status != 'Active':
                user_status = 'skipped_inactive'
            elif member_types is not None and user.member_type not in member_types:
                user_status = 'skipped_visibility'
            elif not user.email or not user.email.strip():
                user_status = 'skipped_no_email'
            elif hasattr(user, 'preferences') and user.preferences and not user.preferences.email_announcements:
                user_status = 'skipped_disabled'
            else:
                user_status = 'pending'  # Will be updated when actually sent

            status_counts[user_status] = status_counts.get(user_status, 0) + 1

            recipients_to_create.append(AnnouncementEmailRecipient(
                email_log=email_log,
                user=user,
                user_name=user.get_display_name() if hasattr(user, 'get_display_name') else user.name,
                user_email=user.email or '',
                user_member_type=user.member_type,
                user_member_status=user.member_status,
                status=user_status
            ))

        # Bulk create recipient records
        AnnouncementEmailRecipient.objects.bulk_create(recipients_to_create)

        log(f"  Total users processed: {len(recipients_to_create)}")
        log(f"  Will send (pending): {status_counts.get('pending', 0)}")
        log(f"  Skipped (inactive): {status_counts.get('skipped_inactive', 0)}")
        log(f"  Skipped (visibility): {status_counts.get('skipped_visibility', 0)}")
        log(f"  Skipped (no email): {status_counts.get('skipped_no_email', 0)}")
        log(f"  Skipped (disabled): {status_counts.get('skipped_disabled', 0)}")

        # Now send emails to eligible users
        log(f"")
        log(f"=" * 60)
        log(f"SENDING EMAILS")
        log(f"=" * 60)
        subject = f"New Announcement: {announcement.title}"
        site_url = get_site_url()

        log(f"Subject: {subject}")
        log(f"From: {settings.DEFAULT_FROM_EMAIL}")
        log(f"Site URL: {site_url}")
        log(f"")

        for user in users_to_email:
            # Get the recipient record to update
            recipient = AnnouncementEmailRecipient.objects.filter(
                email_log=email_log,
                user=user
            ).first()

            if not user.email or not user.email.strip():
                log(f"  SKIP: {user.name} - No email address")
                if recipient:
                    recipient.status = 'skipped_no_email'
                    recipient.save()
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
                if recipient:
                    recipient.status = 'sent'
                    recipient.save()
                log(f"  SENT: {user.name} <{user.email}> ({user.member_type})")

            except Exception as e:
                failed_count += 1
                if recipient:
                    recipient.status = 'failed'
                    recipient.error_message = str(e)
                    recipient.save()
                log(f"  FAIL: {user.name} <{user.email}> - Error: {str(e)}")

        # Update the email log with final counts
        log(f"")
        log(f"=" * 60)
        log(f"COMPLETE")
        log(f"=" * 60)
        log(f"Emails sent: {sent_count}")
        log(f"Emails failed: {failed_count}")

        if failed_count == 0 and sent_count > 0:
            email_log.status = 'completed'
            log(f"Status: COMPLETED")
        elif sent_count > 0 and failed_count > 0:
            email_log.status = 'partial'
            log(f"Status: PARTIAL (some failed)")
        elif sent_count == 0 and failed_count > 0:
            email_log.status = 'failed'
            log(f"Status: FAILED")
        else:
            email_log.status = 'completed'  # No emails to send is still "completed"
            log(f"Status: COMPLETED (no emails to send)")

    except Exception as e:
        log(f"")
        log(f"ERROR: {str(e)}")
        if email_log:
            email_log.status = 'failed'
            email_log.error_message = str(e)
        raise
    finally:
        # Always save console log
        if email_log:
            email_log.emails_sent = sent_count
            email_log.emails_failed = failed_count
            email_log.completed_at = timezone.now()
            email_log.console_log = '\n'.join(console)
            email_log.save()

    logger.info(f"Announcement notification complete. Sent: {sent_count}, Failed: {failed_count}")
    return sent_count


def process_pending_scheduled_announcements():
    """
    Check for and process any scheduled announcements that are now due.
    This should be called from frequently-accessed views (home, announcements).

    Returns:
        int: Number of announcements processed
    """
    now = timezone.now()

    # Find announcements that:
    # 1. Have a publish_at date that has passed
    # 2. Have send_email_on_publish=True
    # 3. Haven't had emails sent yet (email_sent_at is null)
    # 4. Are active
    pending = Announcement.objects.filter(
        publish_at__lte=now,
        send_email_on_publish=True,
        email_sent_at__isnull=True,
        is_active=True,
    )

    processed = 0
    for announcement in pending:
        logger.info(f"[SCHEDULED] Processing scheduled announcement: {announcement.title} (ID: {announcement.id})")

        try:
            # Note: We don't create in-app notifications for announcements because
            # announcements have their own dedicated display system (home page popup,
            # announcements page) with UserAnnouncementView tracking.

            # Send email notifications
            send_announcement_notification(
                announcement,
                initiated_by=announcement.posted_by
            )

            # Mark as sent
            announcement.email_sent_at = timezone.now()
            announcement.send_email_on_publish = False
            announcement.save(update_fields=['email_sent_at', 'send_email_on_publish'])

            processed += 1
            logger.info(f"[SCHEDULED] Successfully processed announcement {announcement.id}")

        except Exception as e:
            logger.error(f"[SCHEDULED] Failed to process announcement {announcement.id}: {e}", exc_info=True)

    return processed


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
