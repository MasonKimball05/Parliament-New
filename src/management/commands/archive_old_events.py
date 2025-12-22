from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from src.models import Event


class Command(BaseCommand):
    help = 'Archive events older than 1 year for record keeping'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be archived without actually archiving',
        )

    def handle(self, *args, **options):
        # Calculate the date one year ago from today
        one_year_ago = timezone.now() - timedelta(days=365)

        # Find events older than 1 year that are not already archived
        old_events = Event.objects.filter(
            date_time__lt=one_year_ago,
            archived=False
        )

        count = old_events.count()

        if options['dry_run']:
            self.stdout.write(
                self.style.WARNING(f'DRY RUN: Would archive {count} events')
            )
            for event in old_events:
                self.stdout.write(
                    f'  - {event.title} ({event.date_time.strftime("%Y-%m-%d")})'
                )
        else:
            # Archive the events
            updated = old_events.update(archived=True, is_active=False)

            self.stdout.write(
                self.style.SUCCESS(f'Successfully archived {updated} events older than 1 year')
            )

            if updated > 0:
                self.stdout.write(
                    self.style.SUCCESS('Archived events are now hidden from the calendar but accessible via admin portal')
                )
