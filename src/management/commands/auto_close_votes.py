"""
Management command to automatically close votes that have passed their voting_ends_at deadline.

Should be run periodically via cron (e.g., every 5 minutes):
    */5 * * * * cd /path/to/parliament && python manage.py auto_close_votes

Or via Django's background task scheduler if configured.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from src.models import Legislation, CommitteeLegislation
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Automatically close votes that have passed their voting_ends_at deadline'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be closed without actually closing',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        now = timezone.now()

        closed_count = 0

        # Process chapter legislation
        chapter_to_close = Legislation.objects.filter(
            voting_closed=False,
            voting_ends_at__isnull=False,
            voting_ends_at__lte=now,
            is_active=True,
        )

        for legislation in chapter_to_close:
            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Would close chapter vote: {legislation.title} "
                    f"(deadline was {legislation.voting_ends_at})"
                )
            else:
                legislation.voting_closed = True
                legislation.voting_ended_at = now
                legislation.set_passed()
                legislation.save()

                closed_count += 1
                logger.info(
                    f"Auto-closed chapter vote: {legislation.title} (ID: {legislation.id})"
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Closed chapter vote: {legislation.title}")
                )

        # Process committee legislation
        committee_to_close = CommitteeLegislation.objects.filter(
            voting_closed=False,
            voting_ends_at__isnull=False,
            voting_ends_at__lte=now,
        )

        for legislation in committee_to_close:
            if dry_run:
                self.stdout.write(
                    f"[DRY-RUN] Would close committee vote: {legislation.committee.code} - {legislation.title} "
                    f"(deadline was {legislation.voting_ends_at})"
                )
            else:
                legislation.voting_closed = True
                legislation.voting_ended_at = now
                legislation.set_passed()
                legislation.save()

                closed_count += 1
                logger.info(
                    f"Auto-closed committee vote: {legislation.committee.code} - {legislation.title} (ID: {legislation.id})"
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Closed committee vote: {legislation.committee.code} - {legislation.title}")
                )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n[DRY-RUN] Would have closed {chapter_to_close.count() + committee_to_close.count()} votes"
            ))
        elif closed_count > 0:
            self.stdout.write(self.style.SUCCESS(
                f"\nAuto-closed {closed_count} votes"
            ))
        else:
            self.stdout.write("No votes ready to close")
