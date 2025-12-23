"""
Management command to reset a user's password to a default format.
Default password format: first letter of first name + last name + user_id
Example: Mason Kimball with ID 73 -> mkimball73
"""
from django.core.management.base import BaseCommand, CommandError
from src.models import ParliamentUser


class Command(BaseCommand):
    help = 'Reset a user password to default format (first_letter + lastname + id) and force password change'

    def add_arguments(self, parser):
        parser.add_argument('user_id', type=str, help='User ID to reset password for')
        parser.add_argument(
            '--password',
            type=str,
            help='Custom password (default: auto-generated from name and ID)'
        )

    def handle(self, *args, **options):
        user_id = options['user_id']
        custom_password = options.get('password')

        try:
            user = ParliamentUser.objects.get(user_id=user_id)
        except ParliamentUser.DoesNotExist:
            raise CommandError(f'User with ID "{user_id}" does not exist')

        # Generate default password if not provided
        if custom_password:
            new_password = custom_password
        else:
            # Parse name to get first letter of first name and last name
            name_parts = user.name.strip().split()
            if len(name_parts) < 2:
                raise CommandError(
                    f'User name "{user.name}" does not have both first and last name. '
                    f'Use --password to set a custom password.'
                )

            first_name = name_parts[0]
            last_name = name_parts[-1]  # Use last part as last name

            # Format: first_letter_of_first_name + lastname + user_id (all lowercase)
            new_password = f"{first_name[0].lower()}{last_name.lower()}{user_id}"

        # Set the new password
        user.set_password(new_password)
        user.force_password_change = True
        user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully reset password for {user.name} (ID: {user_id})\n'
                f'New password: {new_password}\n'
                f'User will be forced to change password on next login.'
            )
        )
