"""
Management command to execute scheduled officer transitions.

Should be run periodically via cron (e.g., every 5 minutes):
    */5 * * * * cd /path/to/parliament && python manage.py execute_scheduled_transitions

Or via Django's background task scheduler if configured.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Execute scheduled officer transitions that are due'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be executed without actually executing',
        )

    def handle(self, *args, **options):
        # Import here to avoid circular imports
        from src.models import SlatingPeriod
        from src.view.slating.transition import execute_transition

        dry_run = options['dry_run']
        now = timezone.now()

        # Find periods with scheduled transitions that are due
        pending_periods = SlatingPeriod.objects.filter(
            officer_transition_at__lte=now,
            officer_transition_completed=False,
        ).exclude(officer_transition_data={})

        if not pending_periods.exists():
            self.stdout.write("No scheduled transitions ready to execute")
            return

        executed_count = 0

        for period in pending_periods:
            transition_data = period.officer_transition_data

            if not transition_data:
                continue

            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Would execute transition for: {period.name} "
                    f"(scheduled for {period.officer_transition_at})"
                )
                self.stdout.write(f"  Positions to transition: {len(transition_data)}")
            else:
                try:
                    results = execute_transition(period, transition_data, performed_by=None)

                    added_count = len(results.get('added', []))
                    removed_count = len(results.get('removed', []))
                    error_count = len(results.get('errors', []))

                    executed_count += 1
                    logger.info(
                        f"Executed scheduled transition for {period.name}: "
                        f"{added_count} added, {removed_count} removed, {error_count} errors"
                    )
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Executed transition for: {period.name} "
                            f"({added_count} added, {removed_count} removed)"
                        )
                    )

                    if results.get('errors'):
                        for error in results['errors']:
                            self.stdout.write(self.style.WARNING(f"  Error: {error}"))

                except Exception as e:
                    logger.error(f"Error executing scheduled transition for {period.name}: {e}")
                    self.stdout.write(
                        self.style.ERROR(f"Error executing transition for {period.name}: {e}")
                    )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n[DRY-RUN] Would have executed {pending_periods.count()} transitions"
            ))
        elif executed_count > 0:
            self.stdout.write(self.style.SUCCESS(
                f"\nExecuted {executed_count} scheduled transitions"
            ))
