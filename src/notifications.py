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
from django.utils.timezone import localtime
from src.models import ParliamentUser, Announcement, UserAnnouncementView, AnnouncementEmailLog, AnnouncementEmailRecipient
import logging

logger = logging.getLogger(__name__)


def _flag_user_email(user, error_message):
    """
    Flag a user's email address as potentially undeliverable after a send failure.
    Logs the event and sets fields that will prompt the user on next login.
    Only flags if not already flagged, to avoid overwriting the original error.
    """
    try:
        from django.utils import timezone as _tz
        if not user.email_flagged:
            user.email_flagged = True
            user.email_flagged_reason = error_message[:500]
            user.email_flagged_at = _tz.now()
            user.save(update_fields=['email_flagged', 'email_flagged_reason', 'email_flagged_at'])
            logger.warning(
                f"[EMAIL FLAG] Flagged email for user {user.username} ({user.email}): {error_message[:200]}"
            )
            try:
                from src.models import ActivityLog
                ActivityLog.log_activity(
                    action_type='other',
                    user=user,
                    description=f"Email address flagged as undeliverable. Error: {error_message[:300]}",
                    metadata={'email': user.email, 'error': error_message[:500]},
                )
            except Exception:
                pass
    except Exception as flag_err:
        logger.error(f"[EMAIL FLAG] Failed to flag email for {getattr(user, 'username', '?')}: {flag_err}")


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
        console.append(f"[{localtime(timezone.now()).strftime('%H:%M:%S.%f')[:-3]}] {msg}")
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
            Q(preferences__prefs__email__announcements=True) | Q(preferences__isnull=True)
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

        # v3.29.10: fetch the poll (if any) and its questions/options ONCE,
        # outside the per-user send loop below, rather than letting the
        # template touch `announcement.poll.questions...` once per recipient
        # — this list can be dozens to hundreds of users, and `manage_announcements.py`
        # already documents this exact N+1 shape (`announcement.poll` was a
        # 5th query per row on the list page before it was fixed there).
        # `getattr(announcement, 'poll', None)` is this codebase's own
        # established idiom for a possibly-absent OneToOneField reverse
        # accessor (see `announcement_polls.py`/`manage_announcements.py`).
        poll = getattr(announcement, 'poll', None)
        poll_questions = None
        poll_url = None
        if poll is not None:
            poll_questions = list(poll.questions.prefetch_related('options').all())
            poll_url = f"{site_url}/announcements/{announcement.id}/poll/"
            log(f"Poll attached: '{poll.title}' ({len(poll_questions)} question(s)) — {poll_url}")

        # Same reasoning as the poll fetch above: `linked_documents` is an
        # M2M, so `announcement.linked_documents.all()` in the template would
        # otherwise be a fresh query per recipient in the loop below. One
        # query for the whole send instead.
        linked_documents = list(announcement.linked_documents.all())
        if linked_documents:
            log(f"Documents attached: {', '.join(d.title for d in linked_documents)}")
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
                    'poll': poll,
                    'poll_questions': poll_questions,
                    'poll_url': poll_url,
                    'linked_documents': linked_documents,
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
                _flag_user_email(user, str(e))

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
        # v3.28.6: fetch the announcement (rather than passing announcement_id
        # straight to get_or_create) so a brand-new view row can be told
        # whether the dismisser was actually part of the target audience —
        # see Announcement.is_in_target_audience.
        announcement = Announcement.objects.get(id=announcement_id)
        announcement.ensure_target_audience_snapshot()
        view, created = UserAnnouncementView.objects.get_or_create(
            user=user,
            announcement=announcement,
            defaults={'counted_in_target': announcement.is_in_target_audience(user)},
        )
        view.dismissed = True
        view.save()
        logger.info(f"User {user.username} dismissed announcement {announcement_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to mark announcement {announcement_id} as dismissed for {user.username}: {str(e)}")
        return False


def send_pledge_welcome_email(user, temp_password):
    """
    Send a welcome email to a newly created pledge with their login credentials
    and an overview of what they have access to on the site.

    Args:
        user: ParliamentUser instance (the new pledge)
        temp_password: The initial password assigned to them (= their username)

    Returns:
        bool: True if sent successfully, False otherwise
    """
    if not user.email:
        logger.info(f"Skipping welcome email for pledge {user.username} — no email address on file")
        return False

    site_url = get_site_url()
    login_url = f"{site_url}/login/"

    subject = f"Welcome to Parliament — Your Account is Ready"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 15px; color: #1f2937; max-width: 600px; margin: 0 auto; padding: 24px;">

  <h2 style="color: #1f2937; margin-bottom: 4px;">Welcome to Parliament, {user.name}!</h2>
  <p style="color: #6b7280; margin-top: 0;">Your pledge account has been created.</p>

  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">

  <h3 style="color: #1f2937; margin-bottom: 8px;">Your Login Credentials</h3>
  <table style="background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; width: 100%; border-collapse: collapse;">
    <tr>
      <td style="padding: 4px 8px; font-weight: bold; color: #374151; width: 130px;">Username</td>
      <td style="padding: 4px 8px; font-family: monospace; color: #111827;">{user.username}</td>
    </tr>
    <tr>
      <td style="padding: 4px 8px; font-weight: bold; color: #374151;">Password</td>
      <td style="padding: 4px 8px; font-family: monospace; color: #111827;">{temp_password}</td>
    </tr>
  </table>
  <p style="color: #dc2626; font-size: 13px; margin-top: 8px;">
    &#9888; You will be required to change your password the first time you log in.
  </p>

  <p style="margin-top: 24px;">
    <a href="{login_url}" style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-weight: bold;">
      Log In to Parliament
    </a>
  </p>

  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">

  <h3 style="color: #1f2937; margin-bottom: 8px;">What You Have Access To</h3>
  <ul style="color: #374151; line-height: 1.8; padding-left: 20px;">
    <li><strong>Announcements</strong> — Stay up to date with chapter news and updates</li>
    <li><strong>Chapter Calendar</strong> — View upcoming events and chapter meetings</li>
    <li><strong>Chapter Documents</strong> — Access documents published to the chapter</li>
    <li><strong>Service Hours</strong> — Track and submit your service hours</li>
    <li><strong>Your Profile</strong> — Update your contact information and preferences</li>
  </ul>

  <p style="color: #6b7280; font-size: 13px; margin-top: 24px;">
    If you have any trouble logging in, reach out to a chapter officer for assistance.
  </p>

  <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
  <p style="color: #9ca3af; font-size: 12px;">
    Parliament &mdash; Alpha Mu Chapter, Beta Theta Pi<br>
    <a href="{site_url}" style="color: #9ca3af;">{site_url}</a>
  </p>

</body>
</html>
"""

    text_body = (
        f"Welcome to Parliament, {user.name}!\n\n"
        f"Your pledge account has been created.\n\n"
        f"LOGIN CREDENTIALS\n"
        f"Username: {user.username}\n"
        f"Password: {temp_password}\n\n"
        f"You will be required to change your password the first time you log in.\n\n"
        f"Log in at: {login_url}\n\n"
        f"WHAT YOU HAVE ACCESS TO\n"
        f"- Announcements: Stay up to date with chapter news\n"
        f"- Chapter Calendar: View upcoming events and meetings\n"
        f"- Chapter Documents: Access documents published to the chapter\n"
        f"- Service Hours: Track and submit your service hours\n"
        f"- Your Profile: Update your contact information and preferences\n\n"
        f"If you have any trouble logging in, reach out to a chapter officer.\n\n"
        f"Parliament — {site_url}"
    )

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send()

        from src.models import ActivityLog
        ActivityLog.log_activity(
            action_type='email_sent',
            user=user,
            description=f"Welcome email sent to new pledge {user.name} ({user.email})",
            metadata={'email_type': 'pledge_welcome', 'recipient': user.email},
        )

        logger.info(f"Welcome email sent to pledge {user.username} ({user.email})")
        return True

    except Exception as e:
        logger.error(f"Failed to send welcome email to pledge {user.username} ({user.email}): {e}")
        _flag_user_email(user, str(e))
        return False
