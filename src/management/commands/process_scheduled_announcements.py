"""
Management command to process scheduled announcements and send emails.

This command should be run periodically (e.g., every minute via cron) to:
1. Find announcements that have become published (publish_at <= now)
2. Check if they have send_email_on_publish=True and email_sent_at is null
3. Send emails for those announcements

Usage:
    python manage.py process_scheduled_announcements

Cron example (run every minute):
    * * * * * cd /path/to/project && /path/to/venv/bin/python manage.py process_scheduled_announcements
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
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
        pending_announcements = Announcement.objects.filter(
            publish_at__lte=now,
            send_email_on_publish=True,
            email_sent_at__isnull=True,
            is_active=True,
        )

        count = pending_announcements.count()

        if count == 0:
            self.stdout.write('No scheduled announcements to process.')
            return

        self.stdout.write(f'Found {count} scheduled announcement(s) to process.')

        for announcement in pending_announcements:
            self.stdout.write(f'  Processing: {announcement.title} (ID: {announcement.id})')
            self.stdout.write(f'    Scheduled for: {announcement.publish_at}')
            self.stdout.write(f'    Visibility: {announcement.visible_to or "All"}')

            if dry_run:
                self.stdout.write(self.style.WARNING('    [DRY RUN] Would send emails'))
                continue

            try:
                # Send in-app notifications first
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

                # Mark as sent
                announcement.email_sent_at = timezone.now()
                announcement.send_email_on_publish = False  # Clear the flag
                announcement.save(update_fields=['email_sent_at', 'send_email_on_publish'])

                self.stdout.write(self.style.SUCCESS(f'    Sent {sent_count} email(s)'))
                logger.info(f"Processed scheduled announcement {announcement.id}: sent {sent_count} emails")

            except Exception as e:
                logger.error(f"Failed to process scheduled announcement {announcement.id}: {e}", exc_info=True)
                self.stdout.write(self.style.ERROR(f'    Error: {e}'))

        self.stdout.write(self.style.SUCCESS(f'Finished processing {count} announcement(s).'))
