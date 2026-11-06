"""
Two-factor authentication in the app (src/mobile/views_two_factor.py): status,
setup + confirm, regenerate backup codes, disable — through the website's code
(src/view/two_factor.py), with its password rule, failure budget, log lines,
notifications, and the app-install revocation rules (src/mobile/revocation.py).

    python manage.py test src.tests.mobile.test_two_factor
"""
import base64
import logging

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django_otp import user_has_device
from django_otp.oath import totp
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import Notification, TwoFactorRequirement
from src.models.mobile import MobileDevice
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'


def code_for(device):
    return str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits)).zfill(device.digits)


class _Capture(logging.Handler):
    """Every record from every logger, to prove a secret is never logged."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class _Base(TestCase):

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = make_user('tfa-user', email='tfa@example.com')
        self.password = 'pw-tfa-user'   # sess_helpers' generated password
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, url, access=None):
        return self.api.get(API + url, **bearer(access or self.access))

    def post(self, url, body=None, access=None):
        return self.api.post(API + url, body or {}, content_type='application/json',
                             **bearer(access or self.access))

    def enable(self):
        """2FA on, as after a setup in the app; this install a TOTP sign-in."""
        totp_device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        backup = StaticDevice.objects.create(user=self.user, name='backup', confirmed=True)
        for c in ('AAAA1111', 'BBBB2222'):
            StaticToken.objects.create(device=backup, token=c)
        MobileDevice.objects.filter(pk=self.device.pk).update(verified_via='password_totp')
        self.device.refresh_from_db()
        return totp_device

    def capture_logs(self):
        handler = _Capture()
        root = logging.getLogger()
        loggers = [root] + [logging.getLogger(n) for n in ('security', 'admin_actions', 'src',
                                                           'src.view.two_factor',
                                                           'src.mobile.views_two_factor')]
        saved = [(lg, lg.level, lg.propagate) for lg in loggers]
        for lg in loggers:
            lg.addHandler(handler)
            lg.setLevel(logging.DEBUG)

        def undo():
            for lg, level, _ in saved:
                lg.removeHandler(handler)
                lg.setLevel(level)
        self.addCleanup(undo)
        return handler


class AuthTests(_Base):

    def test_every_endpoint_needs_a_signed_in_install(self):
        for method, url in [('get', 'account/two-factor/'),
                            ('post', 'account/two-factor/setup/'),
                            ('post', 'account/two-factor/setup/confirm/'),
                            ('post', 'account/two-factor/backup-codes/regenerate/'),
                            ('post', 'account/two-factor/disable/')]:
            r = getattr(self.api, method)(API + url)
            self.assertEqual(r.status_code, 401, url)

    def test_a_browser_session_is_not_enough(self):
        self.api.force_login(self.user)
        self.assertEqual(self.api.get(API + 'account/two-factor/').status_code, 401)

    def test_a_revoked_install_is_refused(self):
        self.device.revoke('test')
        self.assertEqual(self.get('account/two-factor/').status_code, 401)


class StatusTests(_Base):

    def test_off(self):
        r = self.get('account/two-factor/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {
            'enabled': False, 'method': None, 'backup_codes_remaining': 0,
            'backup_codes_acknowledged': False, 'required': False, 'can_disable': False,
            'has_email': True,
        })

    def test_on_never_shows_secrets(self):
        totp_device = self.enable()
        r = self.get('account/two-factor/')
        body = r.json()
        self.assertTrue(body['enabled'])
        self.assertEqual(body['method'], 'totp')
        self.assertEqual(body['backup_codes_remaining'], 2)
        self.assertTrue(body['can_disable'])
        raw = r.content.decode()
        for secret in ('AAAA1111', 'BBBB2222', totp_device.key,
                       base64.b32encode(totp_device.bin_key).decode().rstrip('=')):
            self.assertNotIn(secret, raw)

    def test_required_by_policy_cannot_disable(self):
        self.enable()
        TwoFactorRequirement.objects.create(user=self.user, requirement='required')
        body = self.get('account/two-factor/').json()
        self.assertTrue(body['required'])
        self.assertFalse(body['can_disable'])

    def test_me_says_whether_2fa_is_on(self):
        self.assertIs(self.get('me/').json()['has_2fa'], False)
        self.enable()
        self.assertIs(self.get('me/').json()['has_2fa'], True)


class SetupTests(_Base):

    def start(self):
        r = self.post('account/two-factor/setup/')
        self.assertEqual(r.status_code, 200, r.content)
        return r

    def test_setup_returns_a_new_secret_once(self):
        r = self.start()
        self.assertIn('no-store', r['Cache-Control'])
        body = r.json()
        pending = TOTPDevice.objects.get(user=self.user, confirmed=False)
        self.assertEqual(body['secret'], base64.b32encode(pending.bin_key).decode().rstrip('='))
        self.assertTrue(body['otpauth_url'].startswith('otpauth://totp/'))
        self.assertIn(f'secret={body["secret"]}', body['otpauth_url'])
        self.assertTrue(body['qr'].startswith('data:image/png;base64,'))
        self.assertEqual(body['account_name'], 'tfa-user')
        # Again: a different key, and only one pending device.
        again = self.start().json()
        self.assertNotEqual(again['secret'], body['secret'])
        self.assertEqual(TOTPDevice.objects.filter(user=self.user, confirmed=False).count(), 1)
        self.assertFalse(user_has_device(self.user))

    def test_setup_needs_an_email(self):
        self.user.email = ''
        self.user.save(update_fields=['email'])
        r = self.post('account/two-factor/setup/')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'email_required')
        self.assertFalse(TOTPDevice.objects.exists())

    def test_setup_when_already_on_is_refused(self):
        self.enable()
        r = self.post('account/two-factor/setup/')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'already_enabled')
        self.assertFalse(TOTPDevice.objects.filter(confirmed=False).exists())

    def test_confirm_without_a_setup(self):
        r = self.post('account/two-factor/setup/confirm/', {'code': '123456'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'setup_not_started')

    def test_confirm_needs_a_code(self):
        self.start()
        r = self.post('account/two-factor/setup/confirm/', {})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_input')

    def test_wrong_code(self):
        self.start()
        r = self.post('account/two-factor/setup/confirm/', {'code': '000000'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_code')
        self.assertFalse(user_has_device(self.user))
        self.assertEqual(cache.get(f'mobile_2fa_setup_failures_{self.user.pk}'), 1)

    def test_wrong_codes_are_limited(self):
        self.start()
        pending = TOTPDevice.objects.get(user=self.user, confirmed=False)
        for _ in range(5):
            # Clear django-otp's own throttle so the app's limit is what's tested.
            TOTPDevice.objects.filter(pk=pending.pk).update(throttling_failure_count=0,
                                                            throttling_failure_timestamp=None)
            self.post('account/two-factor/setup/confirm/', {'code': '000000'})
        r = self.post('account/two-factor/setup/confirm/', {'code': code_for(pending)})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'too_many_attempts')
        self.assertFalse(user_has_device(self.user))

    def test_confirm_turns_it_on_and_shows_codes_once(self):
        other_pw, other_access_pw, _ = make_device(self.user, name='Other phone')
        other_pk, other_access_pk, _ = make_device(self.user, verified_via='passkey', name='Passkey phone')
        logs = self.capture_logs()
        secret = self.start().json()['secret']
        pending = TOTPDevice.objects.get(user=self.user, confirmed=False)
        r = self.post('account/two-factor/setup/confirm/', {'code': code_for(pending)})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('no-store', r['Cache-Control'])
        codes = r.json()['backup_codes']
        self.assertEqual(len(codes), 10)
        pending.refresh_from_db()
        self.assertTrue(pending.confirmed)
        stored = set(StaticToken.objects.filter(device__user=self.user).values_list('token', flat=True))
        self.assertEqual(stored, set(codes))
        self.user.refresh_from_db()
        self.assertTrue(self.user.backup_codes_acknowledged)

        # This install proved the code and keeps working, as a TOTP sign-in.
        self.device.refresh_from_db()
        self.assertEqual(self.device.verified_via, 'password_totp')
        self.assertEqual(self.get('me/').status_code, 200)
        # Another password-only install now needs the second factor, as every
        # other browser session is sent to the verify step; a passkey one doesn't.
        r = self.get('me/', access=other_access_pw)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'signed_out_second_factor_required')
        self.assertEqual(self.get('me/', access=other_access_pk).status_code, 200)

        # Neither the secret nor a code is shown again or logged.
        status = self.get('account/two-factor/').content.decode()
        for value in [secret, pending.key] + codes:
            self.assertNotIn(value, status)
            for line in logs.lines:
                self.assertNotIn(value, line)
        self.assertTrue(any('2FA enabled by tfa-user' in line and 'from the app' in line for line in logs.lines))
        r = self.post('account/two-factor/setup/confirm/', {'code': code_for(pending)})
        self.assertEqual(r.json()['error'], 'already_enabled')


class RegenerateTests(_Base):

    def regenerate(self, password=None, access=None):
        return self.post('account/two-factor/backup-codes/regenerate/',
                         {} if password is None else {'password': password}, access=access)

    def test_needs_2fa(self):
        r = self.regenerate(self.password)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'not_enabled')

    def test_needs_the_password(self):
        self.enable()
        r = self.regenerate()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        r = self.regenerate('wrong-password')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertEqual(cache.get(f'cred_change_attempts_{self.user.pk}'), 1)
        self.assertEqual(StaticToken.objects.filter(device__user=self.user).count(), 2)

    def test_regenerate(self):
        self.enable()
        other, other_access, _ = make_device(self.user, verified_via='password_totp', name='Other')
        logs = self.capture_logs()
        r = self.regenerate(self.password)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('no-store', r['Cache-Control'])
        codes = r.json()['backup_codes']
        self.assertEqual(len(codes), 10)
        self.assertNotIn('AAAA1111', codes)
        stored = set(StaticToken.objects.filter(device__user=self.user).values_list('token', flat=True))
        self.assertEqual(stored, set(codes))
        # The website's log line, never the codes.
        self.assertIn('User tfa-user regenerated 2FA backup codes. from the app', logs.lines)
        for code in codes:
            for line in logs.lines:
                self.assertNotIn(code, line)
        # Every other install is signed out (old codes may have been seen); this one is kept.
        other.refresh_from_db()
        self.assertIsNotNone(other.revoked_at)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)
        self.assertEqual(self.get('me/').status_code, 200)
        self.assertEqual(self.get('account/two-factor/').json()['backup_codes_remaining'], 10)

    def test_shares_the_failure_budget(self):
        self.enable()
        for _ in range(5):
            self.regenerate('wrong-password')
        r = self.regenerate(self.password)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'reauth_locked')
        self.assertEqual(StaticToken.objects.filter(device__user=self.user).count(), 2)


class DisableTests(_Base):

    def disable(self, password=None):
        return self.post('account/two-factor/disable/', {} if password is None else {'password': password})

    def test_needs_2fa(self):
        r = self.disable(self.password)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['error'], 'not_enabled')

    def test_wrong_or_missing_password(self):
        self.enable()
        self.assertEqual(self.disable().json()['error'], 'reauth_failed')
        r = self.disable('wrong-password')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertTrue(user_has_device(self.user))
        self.assertEqual(Notification.objects.count(), 0)

    def test_policy_forbids_it(self):
        self.enable()
        TwoFactorRequirement.objects.create(user=self.user, requirement='required')
        r = self.disable(self.password)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'policy_required')
        self.assertTrue(user_has_device(self.user))
        # Refused before the password is looked at: no budget spent.
        self.assertIsNone(cache.get(f'cred_change_attempts_{self.user.pk}'))

    def test_disable(self):
        self.enable()
        other, other_access, _ = make_device(self.user, verified_via='password_totp', name='Other')
        with self.assertLogs('security', 'INFO') as logs:
            r = self.disable(self.password)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'ok': True})
        self.assertFalse(user_has_device(self.user))
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        self.assertFalse(StaticDevice.objects.filter(user=self.user).exists())
        self.assertTrue(any('2FA disabled by tfa-user' in line and 'password re-auth passed) from the app' in line
                            for line in logs.output))
        n = Notification.objects.get(recipient=self.user, notification_type='security')
        self.assertEqual(n.title, 'Two-Factor Authentication was disabled on your account')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Two-Factor Authentication was disabled', mail.outbox[0].subject)
        other.refresh_from_db()
        self.assertIsNotNone(other.revoked_at)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)
        self.assertEqual(self.get('me/').status_code, 200)

    def test_shared_failure_budget_with_the_website(self):
        totp_device = self.enable()
        web = Client()
        web.force_login(self.user)
        session = web.session
        session['otp_device_id'] = totp_device.persistent_id
        session.save()
        for _ in range(4):
            web.post(reverse('two_factor_disable'), {'password': 'wrong-password'})
        self.disable('wrong-password')
        r = self.disable(self.password)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'reauth_locked')
        self.assertTrue(user_has_device(self.user))


class WebFlowUnchangedTests(TestCase):
    """The website's 2FA views after their code moved into shared helpers."""

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = make_user('tfa-web', email='tfa-web@example.com')
        self.web = Client()
        self.web.force_login(self.user)

    def verify_session(self, device):
        session = self.web.session
        session['otp_device_id'] = device.persistent_id
        session.save()

    def test_setup_regenerate_disable(self):
        app_device, _, _ = make_device(self.user, verified_via='password_totp')
        r = self.web.get(reverse('two_factor_setup'))
        self.assertEqual(r.status_code, 200)
        pending = TOTPDevice.objects.get(user=self.user, confirmed=False)
        # 11-06-26: base32 in groups of four (what authenticator apps take), not the hex `key`.
        import base64
        typed = r.context['manual_entry_key'].replace(' ', '')
        self.assertEqual(base64.b32decode(typed + '=' * (-len(typed) % 8)), pending.bin_key)
        # A reload keeps the same pending key on the website.
        self.web.get(reverse('two_factor_setup'))
        self.assertEqual(TOTPDevice.objects.get(user=self.user, confirmed=False).pk, pending.pk)

        r = self.web.post(reverse('two_factor_setup'), {'token': code_for(pending)})
        self.assertRedirects(r, reverse('two_factor_backup_codes_reveal'), fetch_redirect_response=False)
        codes = self.web.session['new_backup_codes']
        self.assertEqual(len(codes), 10)
        self.verify_session(TOTPDevice.objects.get(user=self.user, confirmed=True))

        with self.assertLogs('src.view.two_factor', 'INFO') as logs:
            r = self.web.post(reverse('two_factor_regenerate_backup_codes'))
        self.assertRedirects(r, reverse('two_factor_backup_codes_reveal'), fetch_redirect_response=False)
        self.assertIn('regenerated 2FA backup codes.', logs.output[-1])
        self.assertNotEqual(set(self.web.session['new_backup_codes']), set(codes))
        # The website still signs every app install out when codes are replaced.
        app_device.refresh_from_db()
        self.assertIsNotNone(app_device.revoked_at)

        app_device2, _, _ = make_device(self.user, verified_via='password_totp')
        r = self.web.post(reverse('two_factor_disable'), {'password': 'pw-tfa-web'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(user_has_device(self.user))
        self.assertEqual(mail.outbox[-1].subject.endswith('Two-Factor Authentication was disabled on your account'),
                         True)
        app_device2.refresh_from_db()
        self.assertIsNotNone(app_device2.revoked_at)
