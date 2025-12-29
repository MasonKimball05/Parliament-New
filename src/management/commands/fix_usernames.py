"""
Django management command to fix usernames to standard format
Usage: python manage.py fix_usernames --exclude 73,72,67
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from django.db import IntegrityError

User = get_user_model()


class Command(BaseCommand):
    help = 'Fix all usernames to format: [first_initial][lastname] except specified IDs'

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

        self.stdout.write(self.style.SUCCESS(f'Found {users.count()} users to process'))
        if exclude_ids:
            self.stdout.write(self.style.WARNING(f'Excluding user IDs: {", ".join(map(str, exclude_ids))}'))

        self.stdout.write(self.style.WARNING('Username format: [first letter of first name][last name] (lowercase)'))
        self.stdout.write(self.style.WARNING('Example: Mason Kimball → mkimball'))
        self.stdout.write('')

        # Fix usernames
        updated_count = 0
        skipped_count = 0
        error_count = 0

        for user in users:
            # Parse the name field to generate proper username
            if user.name and user.name.strip():
                name_parts = user.name.strip().split()
                # First letter of first name
                first_initial = name_parts[0][0].lower()
                # Last name is the last part (handles middle names/initials)
                last_name = name_parts[-1].lower().replace('.', '').replace(' ', '')
                new_username = f"{first_initial}{last_name}"
            else:
                # Skip users without a name
                self.stdout.write(self.style.WARNING(f'⚠ Skipped: User ID {user.user_id} has no name'))
                skipped_count += 1
                continue

            # Check if username needs updating
            if user.username == new_username:
                if dry_run:
                    self.stdout.write(f'  Already correct: {user.name} → {user.username}')
                skipped_count += 1
                continue

            # Create display name
            display_name = user.name if user.name else f"ID {user.user_id}"

            if dry_run:
                self.stdout.write(f'Would update: {display_name} | {user.username} → {new_username}')
            else:
                # Check if new username already exists
                if User.objects.filter(username=new_username).exclude(user_id=user.user_id).exists():
                    self.stdout.write(self.style.ERROR(
                        f'✗ Error: {display_name} | {user.username} → {new_username} (username already exists!)'
                    ))
                    error_count += 1
                    continue

                # Update the username
                old_username = user.username
                user.username = new_username

                try:
                    user.save()
                    self.stdout.write(f'✓ Updated: {display_name} | {old_username} → {new_username}')
                    updated_count += 1
                except IntegrityError as e:
                    self.stdout.write(self.style.ERROR(
                        f'✗ Error: {display_name} | {old_username} → {new_username} (database error: {e})'
                    ))
                    error_count += 1

        self.stdout.write('')

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'DRY RUN: Would update {users.count() - skipped_count} usernames'))
            self.stdout.write(self.style.WARNING('Run without --dry-run to actually update usernames'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Successfully updated {updated_count} usernames'))
            if skipped_count > 0:
                self.stdout.write(self.style.WARNING(f'Skipped {skipped_count} users (already correct or no name)'))
            if error_count > 0:
                self.stdout.write(self.style.ERROR(f'Failed to update {error_count} users (see errors above)'))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('IMPORTANT INFORMATION:'))
            self.stdout.write(f'  Username format: [first_initial][lastname] (lowercase)')
            self.stdout.write(f'  Example: Mason Kimball → mkimball')
            self.stdout.write(f'  Users updated: {updated_count}')
            self.stdout.write(f'  Users excluded: {len(exclude_ids)}')
            self.stdout.write(f'  Users skipped: {skipped_count}')
            self.stdout.write(f'  Errors: {error_count}')
