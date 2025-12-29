"""
Django management command to reset all user passwords except specified IDs
Usage: python manage.py reset_all_passwords --exclude 73,72,67
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
import secrets
import string

User = get_user_model()


class Command(BaseCommand):
    help = 'Reset all user passwords except specified IDs and require password change on next login'

    def add_arguments(self, parser):
        parser.add_argument(
            '--exclude',
            type=str,
            default='',
            help='Comma-separated list of user IDs to exclude (e.g., "73,72,67")'
        )
        parser.add_argument(
            '--temp-password',
            type=str,
            default=None,
            help='Set a specific temporary password (default: random 12-char password)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without actually changing it'
        )

    def handle(self, *args, **options):
        # Parse excluded IDs
        exclude_ids = []
        if options['exclude']:
            try:
                exclude_ids = [int(id.strip()) for id in options['exclude'].split(',') if id.strip()]
            except ValueError:
                raise CommandError('Invalid user IDs. Please provide comma-separated numbers.')

        # Generate or use provided temporary password
        if options['temp_password']:
            temp_password = options['temp_password']
        else:
            # Generate random 12-character password
            alphabet = string.ascii_letters + string.digits + "!@#$%&*"
            temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))

        dry_run = options['dry_run']

        # Get all users except excluded ones
        users = User.objects.exclude(user_id__in=exclude_ids)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
            self.stdout.write('')

        self.stdout.write(self.style.SUCCESS(f'Found {users.count()} users to reset'))
        if exclude_ids:
            self.stdout.write(self.style.WARNING(f'Excluding user IDs: {", ".join(map(str, exclude_ids))}'))

        if not dry_run:
            self.stdout.write(self.style.WARNING(f'Temporary password: {temp_password}'))
            self.stdout.write(self.style.WARNING('Users will need to change their password on next login'))

        self.stdout.write('')

        # Reset passwords
        reset_count = 0
        for user in users:
            if dry_run:
                self.stdout.write(f'Would reset: {user.username} (ID: {user.user_id})')
            else:
                # Set the temporary password
                user.set_password(temp_password)

                # Mark that password change is required
                # Use the force_password_change field from the ParliamentUser model
                if hasattr(user, 'force_password_change'):
                    user.force_password_change = True

                # Save the user
                user.save()

                self.stdout.write(f'✓ Reset: {user.username} (ID: {user.user_id})')
                reset_count += 1

        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'DRY RUN: Would reset {users.count()} passwords'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to actually reset passwords'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Successfully reset {reset_count} passwords'))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('IMPORTANT INFORMATION:'))
            self.stdout.write(f'  Temporary Password: {temp_password}')
            self.stdout.write(f'  Users affected: {reset_count}')
            self.stdout.write(f'  Users excluded: {len(exclude_ids)}')
            self.stdout.write('')
            self.stdout.write('Next steps:')
            self.stdout.write('  1. Notify affected users of the temporary password')
            self.stdout.write('  2. Users should change their password on next login')
