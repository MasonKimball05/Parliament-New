"""
Management command to send a test password reset email.
Useful for testing email configuration before deploying to production.
"""
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.forms import PasswordResetForm
from django.test import RequestFactory
from src.models import ParliamentUser


class Command(BaseCommand):
    help = 'Send a test password reset email to a user with an email address'

    def add_arguments(self, parser):
        parser.add_argument(
            'user_id',
            type=str,
            nargs='?',
            help='User ID to send reset email to (optional - will use first user with email if not provided)'
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')

        # Find user
        if user_id:
            try:
                user = ParliamentUser.objects.get(user_id=user_id)
            except ParliamentUser.DoesNotExist:
                raise CommandError(f'User with ID "{user_id}" does not exist')
        else:
            # Find first user with an email
            user = ParliamentUser.objects.filter(email__isnull=False).exclude(email='').first()
            if not user:
                raise CommandError(
                    'No users found with email addresses. '
                    'Please add an email to a user account first.'
                )

        # Check if user has email
        if not user.email:
            raise CommandError(
                f'User {user.name} (ID: {user.user_id}) does not have an email address. '
                f'Please add an email in the admin panel or profile page first.'
            )

        self.stdout.write(f'Sending password reset email to {user.name} ({user.email})...\n')

        # Create a mock request object for generating the reset URL
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_HOST'] = 'localhost:8080'  # For development

        # Use Django's built-in password reset form to send the email
        form = PasswordResetForm({'email': user.email})
        if form.is_valid():
            form.save(
                request=request,
                use_https=False,  # Set to True in production with SSL
                email_template_name='registration/password_reset_email.txt',
                html_email_template_name='registration/password_reset_email.html',
                subject_template_name='registration/password_reset_subject.txt',
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'✓ Test password reset email sent successfully!\n'
                    f'  Recipient: {user.name} ({user.email})\n'
                    f'  User ID: {user.user_id}\n\n'
                    f'Check your email (or console if using console backend).'
                )
            )
        else:
            raise CommandError(f'Failed to send email: {form.errors}')
