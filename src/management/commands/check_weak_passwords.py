"""
Manual entry point for the weak-password audit (see src/weak_password_audit.py
for the full design rationale — candidate list, performance notes, why active
accounts only). Runs automatically every week via Celery Beat
('Weekly weak-password audit' in setup_celery_schedules.py); this command
exists so it can also be run on demand, e.g. right after a bulk
reset_all_passwords run, or when checking whether a specific fix worked.

Usage:
    python manage.py check_weak_passwords
    python manage.py check_weak_passwords --dry-run
"""
from django.core.management.base import BaseCommand

from src.weak_password_audit import audit_active_users_for_weak_passwords


class Command(BaseCommand):
    help = 'Check active users\' current passwords against known-weak values and file security alerts for matches'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would be flagged without creating LoginAlert rows',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - no alerts will be created'))

        summary = audit_active_users_for_weak_passwords(dry_run=dry_run)

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f"Checked: {summary['checked']} active user(s)"))
        if summary['already_flagged_skipped']:
            self.stdout.write(
                f"Skipped: {summary['already_flagged_skipped']} user(s) with an "
                f"existing open weak-password alert (resolve it in admin-v2 "
                f"Security Alerts to have them re-checked next run)"
            )
        if summary['flagged']:
            style = self.style.WARNING if dry_run else self.style.ERROR
            verb = 'Would flag' if dry_run else 'Flagged'
            self.stdout.write(style(f"{verb}: {summary['flagged']} user(s) with a known-weak password"))
            if not dry_run:
                self.stdout.write('  See admin-v2 → Security Alerts for details.')
        else:
            self.stdout.write(self.style.SUCCESS('No known-weak passwords found.'))
