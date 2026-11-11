"""
Rebuild the pledge class pages for classes initiated before 11-10-26, when
initiation started saving them (src/pledge_class_archive.py).

Each past initiation is found in the activity log; the class is rebuilt as
the education dashboard stood at that moment (only tasks that existed then,
meetings held and point adjustments made by then). Initiation keeps members'
ids, so their task results, quiz answers and attendance are still linked.
A class already saved is skipped, so running it twice is harmless.

    python manage.py backfill_pledge_class --dry-run   # show what it would save
    python manage.py backfill_pledge_class             # save it
    python manage.py backfill_pledge_class --log-id 1234   # one initiation only
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from src.models import ActivityLog
from src.pledge_class_archive import backfill_from_log


class Command(BaseCommand):
    help = 'Rebuild pledge class pages for classes initiated before they were saved automatically.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show what would be saved; save nothing.')
        parser.add_argument('--log-id', type=int, help='Only the initiation in this ActivityLog row.')

    def handle(self, *args, dry_run=False, log_id=None, **options):
        logs = ActivityLog.objects.filter(metadata__action='initiate_pledges').order_by('timestamp')
        if log_id:
            logs = logs.filter(pk=log_id)
        if not logs.exists():
            self.stdout.write('No initiations found in the activity log.')
            return
        saved = 0
        for log in logs:
            with transaction.atomic():
                results = backfill_from_log(log, save=not dry_run)
            stamp = timezone.localtime(log.timestamp).strftime('%Y-%m-%d %H:%M')
            if not results:
                self.stdout.write(f'{stamp} (log {log.pk}): already saved, or nothing to rebuild. Skipped.')
                continue
            for committee, result in results:
                data = result if dry_run else result.data
                name = result['name'] if dry_run else result.name
                verb = 'Would save' if dry_run else 'Saved'
                self.stdout.write(self.style.SUCCESS(
                    f'{stamp} (log {log.pk}): {verb} "{name}" on {committee.name}: '
                    f'{len(data["pledges"])} members, {len(data["tasks"])} tasks, {len(data["meetings"])} meetings'))
                for p in data['pledges']:
                    pct = f' ({p["points_percent"]}%)' if p['points_percent'] is not None else ''
                    self.stdout.write(f'    {p["name"]}: {p["points"]}/{p["max_points"]} pts{pct}, '
                                      f'{p["completed"]}/{p["total"]} required tasks')
                saved += 0 if dry_run else 1
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run: nothing saved.'))
        else:
            self.stdout.write(f'{saved} class page(s) saved.')
