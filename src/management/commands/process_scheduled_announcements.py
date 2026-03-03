"""
Management command to process scheduled announcements and send emails.

This command should be run periodically (e.g., every 5 minutes via cron) to:
1. Find announcements that have become published (publish_at <= now)
2. Check if they have send_email_on_publish=True and email_sent_at is null
3. Send emails for those announcements

Usage:
    python manage.py process_scheduled_announcements

Cron example (run every 5 minutes):
    */5 * * * * cd /path/to/project && /path/to/venv/bin/python manage.py process_scheduled_announcements

Race condition protection:
    This command uses database row locking (select_for_update with skip_locked) to prevent
    multiple cron jobs from processing the same announcement simultaneously.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from src.models import Announcement
from src.notifications import send_announcement_notification
from src.notification_service import notify_all_active_members
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Process scheduled announcements and send email notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be processed without actually sending emails',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        # Find announcements that:
        # 1. Have a publish_at date that has passed
        # 2. Have send_email_on_publish=True
        # 3. Haven't had emails sent yet (email_sent_at is null)
        # 4. Are active
        pending_query = Announcement.objects.filter(
            publish_at__lte=now,
            send_email_on_publish=True,
            email_sent_at__isnull=True,
            is_active=True,
        )

        # Get count first (without locking) for display
        count = pending_query.count()

        if count == 0:
            self.stdout.write('No scheduled announcements to process.')
            return

        self.stdout.write(f'Found {count} scheduled announcement(s) to process.')

        # Process each announcement individually with row-level locking
        # to prevent race conditions with concurrent cron jobs
        for announcement_id in pending_query.values_list('id', flat=True):
            self._process_announcement(announcement_id, dry_run)

        self.stdout.write(self.style.SUCCESS(f'Finished processing.'))

    def _process_announcement(self, announcement_id, dry_run):
        """
        Process a single announcement with row-level locking.
        Uses select_for_update with skip_locked to prevent race conditions.
        """
        try:
            with transaction.atomic():
                # Lock this specific announcement row
                # skip_locked=True means if another process has the lock, we skip it
                announcement = Announcement.objects.select_for_update(
                    skip_locked=True
                ).filter(
                    id=announcement_id,
                    send_email_on_publish=True,  # Re-check in case it changed
                    email_sent_at__isnull=True,  # Re-check in case it changed
                ).first()

                if not announcement:
                    # Either already processed by another job, or locked by another process
                    self.stdout.write(f'  Skipping ID {announcement_id}: already processed or locked')
                    return

                self.stdout.write(f'  Processing: {announcement.title} (ID: {announcement.id})')
                self.stdout.write(f'    Scheduled for: {announcement.publish_at}')
                self.stdout.write(f'    Visibility: {announcement.visible_to or "All"}')

                if dry_run:
                    self.stdout.write(self.style.WARNING('    [DRY RUN] Would send emails'))
                    return

                # Mark as being processed BEFORE sending (prevents duplicates)
                # We set email_sent_at now to claim this announcement
                announcement.email_sent_at = timezone.now()
                announcement.send_email_on_publish = False
                announcement.save(update_fields=['email_sent_at', 'send_email_on_publish'])

            # Now send notifications OUTSIDE the transaction (emails can be slow)
            # The announcement is already marked as sent, so no other job will pick it up
            try:
                notify_all_active_members(
                    'announcement',
                    f'New Announcement: {announcement.title}',
                    message=announcement.content[:100],
                    link='/announcements/',
                    source_type='Announcement',
                    source_id=announcement.id,
                )
                self.stdout.write('    In-app notifications sent')
            except Exception as e:
                logger.error(f"Failed to create in-app notifications for announcement {announcement.id}: {e}")
                self.stdout.write(self.style.WARNING(f'    In-app notification failed: {e}'))

            # Send email notifications
            sent_count = send_announcement_notification(
                announcement,
                initiated_by=announcement.posted_by
            )

            self.stdout.write(self.style.SUCCESS(f'    Sent {sent_count} email(s)'))
            logger.info(f"Processed scheduled announcement {announcement.id}: sent {sent_count} emails")

        except Exception as e:
            logger.error(f"Failed to process scheduled announcement {announcement_id}: {e}", exc_info=True)
            self.stdout.write(self.style.ERROR(f'    Error processing ID {announcement_id}: {e}'))
