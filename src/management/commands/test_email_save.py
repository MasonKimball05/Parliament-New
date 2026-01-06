"""
Management command to test email saving functionality
Run with: python manage.py test_email_save <user_id> <email>
"""
from django.core.management.base import BaseCommand
from src.models import ParliamentUser
from django.db import connection


class Command(BaseCommand):
    help = 'Tests email saving functionality for diagnosing production issues'

    def add_arguments(self, parser):
        parser.add_argument('user_id', type=str, help='User ID to test')
        parser.add_argument('email', type=str, help='Email to set')

    def handle(self, *args, **kwargs):
        user_id = kwargs['user_id']
        email = kwargs['email']

        self.stdout.write(self.style.SUCCESS('='*60))
        self.stdout.write(self.style.SUCCESS('Email Save Diagnostic Test'))
        self.stdout.write(self.style.SUCCESS('='*60))

        try:
            # Get user
            self.stdout.write(f'\n1. Looking up user with ID: {user_id}')
            user = ParliamentUser.objects.get(user_id=user_id)
            self.stdout.write(self.style.SUCCESS(f'   ✓ Found user: {user.get_display_name()}'))
            self.stdout.write(f'   Current email: {user.email if user.email else "[None]"}')

            # Check database connection
            self.stdout.write(f'\n2. Database Connection:')
            self.stdout.write(f'   Engine: {connection.settings_dict["ENGINE"]}')
            self.stdout.write(f'   Database: {connection.settings_dict["NAME"]}')
            self.stdout.write(f'   Host: {connection.settings_dict.get("HOST", "localhost")}')

            # Backup old email
            old_email = user.email

            # Try to save new email
            self.stdout.write(f'\n3. Attempting to save email: {email}')
            user.email = email
            user.save()
            self.stdout.write(self.style.SUCCESS('   ✓ save() called successfully'))

            # Verify in database
            self.stdout.write(f'\n4. Verifying save...')
            user.refresh_from_db()
            self.stdout.write(f'   Email after refresh_from_db(): {user.email if user.email else "[None]"}')

            # Direct SQL query to verify
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT email FROM src_parliamentuser WHERE user_id = %s",
                    [user_id]
                )
                row = cursor.fetchone()
                db_email = row[0] if row else None
                self.stdout.write(f'   Email from direct SQL query: {db_email if db_email else "[None]"}')

            # Check if save was successful
            if user.email == email:
                self.stdout.write(self.style.SUCCESS(f'\n✅ SUCCESS: Email saved correctly!'))
                self.stdout.write(f'   Previous: {old_email if old_email else "[None]"}')
                self.stdout.write(f'   Current:  {user.email}')
            else:
                self.stdout.write(self.style.ERROR(f'\n❌ FAILURE: Email not saved correctly!'))
                self.stdout.write(f'   Expected: {email}')
                self.stdout.write(f'   Got:      {user.email if user.email else "[None]"}')

            # Check model fields
            self.stdout.write(f'\n5. Model Information:')
            email_field = user._meta.get_field('email')
            self.stdout.write(f'   Field type: {type(email_field).__name__}')
            self.stdout.write(f'   Max length: {email_field.max_length}')
            self.stdout.write(f'   Null allowed: {email_field.null}')
            self.stdout.write(f'   Blank allowed: {email_field.blank}')

            # Restore old email if test mode
            if old_email != email:
                self.stdout.write(f'\n6. Restoring original email...')
                user.email = old_email
                user.save()
                user.refresh_from_db()
                self.stdout.write(self.style.SUCCESS(f'   ✓ Restored to: {user.email if user.email else "[None]"}'))

        except ParliamentUser.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'\n❌ ERROR: User with ID {user_id} not found'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n❌ ERROR: {str(e)}'))
            import traceback
            self.stdout.write(traceback.format_exc())

        self.stdout.write(self.style.SUCCESS('\n' + '='*60))
