"""
Management command to check for scheduled maintenance and start it if due.

Should be run frequently via cron (e.g., every minute):
    * * * * * cd /path/to/parliament && python manage.py check_scheduled_maintenance

This will:
1. Check if any scheduled maintenance is due to start
2. Enable the maintenance_mode feature flag
3. Send notification email to the configured address
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from src.models_feature_flags import ScheduledMaintenance
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Check for scheduled maintenance and start it if due'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without actually starting maintenance',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Get maintenance that should start now
        pending = ScheduledMaintenance.get_pending_maintenance()

        if not pending:
            self.stdout.write("No scheduled maintenance pending")
            return

        self.stdout.write(
            f"Found pending maintenance: {pending.title} "
            f"(scheduled for {pending.scheduled_start})"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY-RUN] Would start maintenance: {pending.title}"
            ))
            if pending.notify_email:
                self.stdout.write(self.style.WARNING(
                    f"[DRY-RUN] Would send notification to: {pending.notify_email}"
                ))
            return

        # Start the maintenance
        try:
            pending.start_maintenance()
            logger.info(f"Started scheduled maintenance: {pending.title} (ID: {pending.id})")
            self.stdout.write(self.style.SUCCESS(
                f"Started maintenance: {pending.title}"
            ))

            if pending.notify_email:
                self.stdout.write(self.style.SUCCESS(
                    f"Notification email sent to: {pending.notify_email}"
                ))

        except Exception as e:
            logger.error(f"Failed to start scheduled maintenance: {e}")
            self.stdout.write(self.style.ERROR(
                f"Failed to start maintenance: {e}"
            ))
