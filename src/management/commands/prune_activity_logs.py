from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from src.models import ActivityLog


# Categories considered security-sensitive — kept longer by default.
SECURITY_CATEGORIES = {'auth', 'user'}


class Command(BaseCommand):
    help = (
        'Delete ActivityLog entries older than a configurable threshold. '
        'Security-sensitive categories (auth, user) use a separate, longer retention window.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=365,
            help='Retain general logs for this many days (default: 365).',
        )
        parser.add_argument(
            '--security-days',
            type=int,
            default=730,
            help='Retain auth/user logs for this many days (default: 730 / 2 years).',
        )
        parser.add_argument(
            '--category',
            type=str,
            default=None,
            help=(
                'Only prune a specific category (e.g. "document", "event"). '
                'Uses --days as the threshold regardless of category. '
                'If omitted, all categories are pruned with their respective thresholds.'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without deleting anything.',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        days = options['days']
        security_days = options['security_days']
        category = options['category']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no records will be deleted.'))

        if category:
            # Single-category mode: apply --days threshold to the specified category only.
            cutoff = now - timedelta(days=days)
            qs = ActivityLog.objects.filter(action_category=category, timestamp__lt=cutoff)
            self._prune(qs, label=f'category={category}', cutoff=cutoff, dry_run=dry_run)
        else:
            # Full prune: security categories get the longer window.
            general_cutoff = now - timedelta(days=days)
            security_cutoff = now - timedelta(days=security_days)

            general_qs = ActivityLog.objects.filter(
                timestamp__lt=general_cutoff,
            ).exclude(action_category__in=SECURITY_CATEGORIES)

            security_qs = ActivityLog.objects.filter(
                action_category__in=SECURITY_CATEGORIES,
                timestamp__lt=security_cutoff,
            )

            self._prune(
                general_qs,
                label=f'general (non-security) categories older than {days}d',
                cutoff=general_cutoff,
                dry_run=dry_run,
            )
            self._prune(
                security_qs,
                label=f'auth/user categories older than {security_days}d',
                cutoff=security_cutoff,
                dry_run=dry_run,
            )

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run complete — nothing was deleted.'))

    def _prune(self, qs, label, cutoff, dry_run):
        count = qs.count()

        if count == 0:
            self.stdout.write(f'  {label}: nothing to prune.')
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'  DRY RUN: would delete {count} record(s) — {label} (cutoff: {cutoff.date()})')
            )
            # Show a sample of up to 10 records so the operator can verify.
            for entry in qs.order_by('timestamp')[:10]:
                user = entry.user.name if entry.user else 'system'
                self.stdout.write(
                    f'    [{entry.timestamp.strftime("%Y-%m-%d")}] {entry.action_category}/{entry.action_type} — {user}'
                )
            if count > 10:
                self.stdout.write(f'    … and {count - 10} more.')
        else:
            deleted, _ = qs.delete()
            self.stdout.write(
                self.style.SUCCESS(f'  Deleted {deleted} record(s) — {label} (cutoff: {cutoff.date()})')
            )
