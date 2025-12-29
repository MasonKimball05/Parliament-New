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

        dry_run = options['dry_run']

        # Get all users except excluded ones
        users = User.objects.exclude(user_id__in=exclude_ids)

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No changes will be made'))
            self.stdout.write('')

        self.stdout.write(self.style.SUCCESS(f'Found {users.count()} users to reset'))
        if exclude_ids:
            self.stdout.write(self.style.WARNING(f'Excluding user IDs: {", ".join(map(str, exclude_ids))}'))

        self.stdout.write(self.style.WARNING('Password format: [first letter of first name][last name][user_id]'))
        self.stdout.write(self.style.WARNING('Example: Mason Kimball (ID 73) → mkimball73'))
        self.stdout.write('')

        # Reset passwords
        reset_count = 0
        for user in users:
            # Generate password: first letter of first name + last name + user_id
            # Example: Mason Kimball with ID 73 → mkimball73

            # Parse the name field (format: "First Middle Last" or "First Last")
            if user.name and user.name.strip():
                name_parts = user.name.strip().split()
                # First letter of first name
                first_initial = name_parts[0][0].lower()
                # Last name is the last part (handles middle names/initials)
                last_name = name_parts[-1].lower().replace('.', '').replace(' ', '')
            else:
                # Fallback if name is empty
                first_initial = 'x'
                last_name = 'user'

            new_password = f"{first_initial}{last_name}{user.user_id}"

            # Create display name - show full name if available, otherwise username
            if user.name and user.name.strip():
                display_name = f"{user.name} ({user.username})"
            else:
                display_name = f"{user.username} (No name in database)"

            if dry_run:
                self.stdout.write(f'Would reset: {display_name} [ID: {user.user_id}] → {new_password}')
            else:
                # Set the new password
                user.set_password(new_password)

                # Mark that password change is required
                # Use the force_password_change field from the ParliamentUser model
                if hasattr(user, 'force_password_change'):
                    user.force_password_change = True

                # Save the user
                user.save()

                self.stdout.write(f'✓ Reset: {display_name} [ID: {user.user_id}] → {new_password}')
                reset_count += 1

        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'DRY RUN: Would reset {users.count()} passwords'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to actually reset passwords'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Successfully reset {reset_count} passwords'))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('IMPORTANT INFORMATION:'))
            self.stdout.write(f'  Password format: [first letter of first name][last name][user_id]')
            self.stdout.write(f'  Example: Mason Kimball (ID 73) → mkimball73')
            self.stdout.write(f'  Users affected: {reset_count}')
            self.stdout.write(f'  Users excluded: {len(exclude_ids)}')
            self.stdout.write('')
            self.stdout.write('Next steps:')
            self.stdout.write('  1. Notify affected users of the password format')
            self.stdout.write('  2. Users must change their password on next login')
            self.stdout.write('  3. Passwords are case-sensitive (all lowercase)')
