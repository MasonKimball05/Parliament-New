from django.core.management.base import BaseCommand
from django.utils import timezone
from src.models import ChatChannelPermission


class Command(BaseCommand):
    help = 'Remove chat channel permissions whose expires_at has passed'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        expired = ChatChannelPermission.objects.filter(
            expires_at__isnull=False,
            expires_at__lte=timezone.now(),
        )

        count = expired.count()
        if count == 0:
            self.stdout.write('No expired permissions found.')
            return

        if options['dry_run']:
            self.stdout.write(f'Would delete {count} expired permission(s):')
            for perm in expired.select_related('user', 'channel'):
                self.stdout.write(f'  - {perm.user.name if perm.user else "role"} → {perm.channel.name} (expired {perm.expires_at})')
        else:
            expired.delete()
            self.stdout.write(self.style.SUCCESS(f'Deleted {count} expired permission(s).'))
