"""
Create, reset or disable the Apple App Review demo account.

    manage.py create_app_review_account                 # create or reset `appreview`
    manage.py create_app_review_account --username demo  # another name
    manage.py create_app_review_account --disable       # after review

The account is an ordinary Member: no roles, no admin flag, no email, onboarding
done, no forced password change. A fresh random password is printed ONCE — copy
it into App Store Connect's sign-in information; it is not stored anywhere else.
Running it again resets the password (and removes any authenticator the reviewer
enrolled, so password-only sign-in works again).

What makes the account special lives in ``src/app_review.py`` and is keyed on
``settings.APP_REVIEW_USERNAMES``, so the username must be listed there first —
this command refuses otherwise rather than create a normal-looking member that
would need 2FA, appear in the directory and see everyone's contact details.

``--disable`` deactivates it (is_active False, member_status Inactive, password
unusable, app devices revoked). Leave the env var in place afterwards so the row
stays out of lists; re-run without ``--disable`` to bring it back.
"""
import secrets
import string

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from src.app_review import review_usernames

DEFAULT_USERNAME = 'appreview'
DISPLAY_NAME = 'App Review'
PASSWORD_LENGTH = 24
#: Letters and digits only, minus look-alikes — a reviewer types it by hand.
PASSWORD_ALPHABET = ''.join(c for c in string.ascii_letters + string.digits if c not in 'Il1O0o')


def generate_password(length=PASSWORD_LENGTH):
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


class Command(BaseCommand):
    help = 'Create/reset (or --disable) the Apple App Review demo account named in APP_REVIEW_USERNAMES.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default=DEFAULT_USERNAME,
                            help=f'Username for the review account (default: {DEFAULT_USERNAME}).')
        parser.add_argument('--disable', action='store_true',
                            help='Deactivate the account after App Review is finished.')

    def handle(self, *args, **options):
        username = (options['username'] or '').strip().lower()
        if not username:
            raise CommandError('A username is required.')
        env_line = f'APP_REVIEW_USERNAMES={username}'

        if username not in review_usernames():
            configured = ', '.join(sorted(review_usernames())) or '(empty)'
            raise CommandError(
                f'"{username}" is not in APP_REVIEW_USERNAMES (currently {configured}).\n'
                f'Add this line to the server environment (.env), restart the app\n'
                f'(gunicorn and the worker), then run this command again:\n\n'
                f'    {env_line}\n')

        User = get_user_model()
        if options['disable']:
            self._disable(User, username)
            return
        self._create_or_reset(User, username, env_line)

    # ── create / reset ─────────────────────────────────────────────────────

    def _create_or_reset(self, User, username, env_line):
        from src.models.users import generate_member_uid

        password = generate_password()
        with transaction.atomic():
            user = User.objects.filter(username__iexact=username).first()
            created = user is None
            if created:
                user = User(user_id=generate_member_uid(), username=username,
                            name=DISPLAY_NAME, member_type='Member')
            user.username = username
            user.name = DISPLAY_NAME
            user.preferred_name = ''
            user.member_type = 'Member'
            user.member_status = 'Active'
            user.is_active = True
            user.is_admin = False
            user.is_quarantined = False
            user.force_password_change = False
            user.has_default_password = False
            user.onboarding_complete = True
            user.email = None          # no real inbox; nothing is ever sent to it
            user.other_email = None
            user.phone_number = ''
            user.house = ''
            user.set_password(password)
            user.save()
            if user.roles.exists():
                user.roles.clear()
                # The roles signal may have re-derived member_type; pin it back.
                User.objects.filter(pk=user.pk).update(member_type='Member', is_admin=False)
            self._remove_second_factors(user)

        from src.mobile.revocation import revoke_user_devices
        revoke_user_devices(user.pk, 'app review account reset')
        try:
            from src.utils.security_utils import clear_lockouts_for
            clear_lockouts_for(username=username)
        except Exception as exc:   # a lockout we can't clear is not fatal here
            self.stderr.write(f'Note: could not clear login lockouts ({exc}).')

        on_committees = (user.committees.exists() or user.chair_roles.exists())
        verb = 'Created' if created else 'Reset'
        self.stdout.write(self.style.SUCCESS(f'{verb} the App Review account.'))
        self.stdout.write('')
        self.stdout.write(f'  Username: {username}')
        self.stdout.write(f'  Password: {password}')
        self.stdout.write('')
        self.stdout.write('This password is shown once. Put it in App Store Connect → App Review')
        self.stdout.write('Information → Sign-in required. Running this again makes a new one.')
        self.stdout.write('')
        self.stdout.write('The server environment must keep this line (it is what exempts the')
        self.stdout.write('account from 2FA and hides it and contact details):')
        self.stdout.write(f'    {env_line}')
        if on_committees:
            self.stdout.write(self.style.WARNING(
                'Warning: the account is on a committee. Remove it — committee rosters '
                'and votes are not filtered for review accounts.'))
        site = getattr(settings, 'SITE_URL', '')
        if site:
            self.stdout.write(f'Sign-in: the iOS app, or {site}/login/')

    @staticmethod
    def _remove_second_factors(user):
        """An authenticator the reviewer enrolled would make the next reviewer's
        password-only sign-in ask for a code nobody has."""
        from django_otp.plugins.otp_static.models import StaticDevice
        from django_otp.plugins.otp_totp.models import TOTPDevice
        TOTPDevice.objects.filter(user=user).delete()
        StaticDevice.objects.filter(user=user).delete()

    # ── disable ────────────────────────────────────────────────────────────

    def _disable(self, User, username):
        user = User.objects.filter(username__iexact=username).first()
        if user is None:
            raise CommandError(f'No account named "{username}".')
        user.is_active = False
        user.member_status = 'Inactive'
        user.set_unusable_password()
        user.save(update_fields=['is_active', 'member_status', 'password'])
        from src.mobile.revocation import revoke_user_devices
        revoked = revoke_user_devices(user.pk, 'app review account disabled')
        self.stdout.write(self.style.SUCCESS(
            f'Disabled "{username}" ({revoked} app sign-in(s) revoked). '
            f'Re-run without --disable to bring it back with a new password.'))
