"""
POST /api/mobile/v1/auth/login/ and auth/verify/ (src/mobile/views_auth.py).

The password checks are the web's (src/login_pipeline.py), so these also prove
the two doors share one lockout budget.
"""
from datetime import timedelta

from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

from src.models import LoginHistory, TwoFactorRequirement
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.models_feature_flags import SiteSetting
from src.tests.mobile.base import PASSWORD, MobileTestCase, make_user, totp_code, totp_device


class PasswordLoginTests(MobileTestCase):

    def test_no_2fa_returns_tokens_and_no_session(self):
        user = make_user('m100')
        r = self.login(user.username)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertFalse(body['mfa_required'])
        for key in ('access_token', 'refresh_token', 'device_id', 'access_expires_at'):
            self.assertIn(key, body)
        device = MobileDevice.objects.get(user=user)
        self.assertEqual(device.verified_via, 'password')
        self.assertEqual(device.platform, 'ios')
        self.assertEqual(device.device_name, 'Test iPhone')
        # No session, no cookie: the app never holds a web session from sign-in.
        self.assertNotIn('sessionid', r.cookies)
        self.assertEqual(Session.objects.count(), 0)
        # The complete sign-in is recorded once, like a web login.
        self.assertEqual(LoginHistory.objects.filter(user=user, status='success').count(), 1)
        self.assertEqual(self.get_me(body['access_token']).status_code, 200)

    def test_login_by_email_address(self):
        user = make_user('m101')
        r = self.login(user.email.upper())
        self.assertEqual(r.status_code, 200, r.content)

    def test_wrong_password_is_generic_401(self):
        user = make_user('m102')
        r1 = self.login(user.username, 'wrong-password')
        r2 = self.login('nobody-at-all', 'wrong-password')
        self.assertEqual(r1.status_code, 401)
        self.assertEqual(r1.json(), {'error': 'invalid_credentials'})
        self.assertEqual(r2.json(), r1.json())   # no user enumeration
        self.assertFalse(MobileDevice.objects.exists())

    def test_bad_input_is_400(self):
        make_user('m103')
        for body in ({}, {'identifier': 'm103user'},
                     {'identifier': 'm103user', 'password': PASSWORD},              # no platform
                     {'identifier': 'm103user', 'password': PASSWORD, 'platform': 'palm'},
                     {'identifier': ['x'], 'password': PASSWORD, 'platform': 'ios'}):
            r = self.post('auth/login/', body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json()['error'], 'invalid_request')

    def test_five_app_failures_lock_the_web_too(self):
        user = make_user('m104')
        for _ in range(4):
            self.assertEqual(self.login(user.username, 'nope').status_code, 401)
        r = self.login(user.username, 'nope')
        self.assertEqual(r.status_code, 423)
        self.assertGreater(r.json()['retry_after'], 0)
        # Correct password in the app: still locked.
        self.assertEqual(self.login(user.username).status_code, 423)
        # And the web login page refuses the correct password from this IP.
        from django.test import Client
        web = Client()
        web.post(reverse('login'), {'username': user.username, 'password': PASSWORD})
        self.assertNotIn('_auth_user_id', web.session)

    def test_web_lockout_locks_the_app(self):
        from django.test import Client
        user = make_user('m105')
        web = Client()
        for _ in range(5):
            web.post(reverse('login'), {'username': user.username, 'password': 'nope'})
        self.assertEqual(self.login(user.username).status_code, 423)

    def test_account_lock_is_honoured_when_signing_in_by_email(self):
        user = make_user('m106')
        from src.login_pipeline import get_account_lockout_key
        cache.set(get_account_lockout_key(user.username), timezone.now() + timedelta(minutes=10), 600)
        self.assertEqual(self.login(user.username).status_code, 423)
        self.assertEqual(self.login(user.email).status_code, 423)

    def test_quarantined_is_403_generic(self):
        user = make_user('m107', is_quarantined=True)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'access_denied')
        self.assertFalse(MobileDevice.objects.exists())

    def test_blacklisted_ip_is_403(self):
        from src.models import IPBlacklist
        user = make_user('m108')
        IPBlacklist.objects.create(ip_address='127.0.0.1', reason='test', is_active=True)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'blocked')   # InputSanitizationMiddleware gate, as JSON
        # Past a stale negative in the middleware's cache, the pipeline's own check refuses.
        cache.set('ip_blacklisted_127.0.0.1', False, 300)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'access_denied')
        self.assertFalse(MobileDevice.objects.exists())

    def test_disabled_account_is_generic_401(self):
        user = make_user('m109', is_active=False)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {'error': 'invalid_credentials'})

    def test_policy_requires_2fa_but_none_set_up(self):
        user = make_user('m110')
        TwoFactorRequirement.objects.create(user=user, requirement='required')
        r = self.login(user.username)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()['mfa_setup_required'])
        self.assertFalse(MobileDevice.objects.exists())

    def test_policy_all_members_requires_setup(self):
        user = make_user('m111')
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode', defaults={'value': 'all_members', 'setting_type': 'string'})
        self.assertEqual(self.login(user.username).status_code, 403)

    def test_has_device_returns_ticket_and_no_device(self):
        user = make_user('m112')
        totp_device(user)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['mfa_required'])
        self.assertTrue(body['ticket'])
        self.assertNotIn('access_token', body)
        self.assertFalse(MobileDevice.objects.exists())
        # The password step alone is NOT a sign-in yet.
        self.assertFalse(LoginHistory.objects.filter(user=user, status='success').exists())


class VerifyTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('m200')
        self.device = totp_device(self.user)

    def ticket(self):
        r = self.login(self.user.username, platform='android', device_name='Pixel')
        return r.json()['ticket']

    def test_totp_issues_device(self):
        r = self.post('auth/verify/', {'ticket': self.ticket(), 'code': totp_code(self.device)})
        self.assertEqual(r.status_code, 200, r.content)
        d = MobileDevice.objects.get(user=self.user)
        self.assertEqual(d.verified_via, 'password_totp')
        self.assertEqual(d.platform, 'android')          # carried from the login step
        self.assertEqual(d.device_name, 'Pixel')
        self.assertEqual(self.get_me(r.json()['access_token']).status_code, 200)
        self.assertEqual(LoginHistory.objects.filter(user=self.user, status='success').count(), 1)

    def test_backup_code_is_consumed(self):
        static = StaticDevice.objects.create(user=self.user, name='backup', confirmed=True)
        StaticToken.objects.create(device=static, token='A1B2C3D4')
        StaticToken.objects.create(device=static, token='99887766')
        r = self.post('auth/verify/', {'ticket': self.ticket(), 'code': 'a1b2c3d4'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['backup_codes_remaining'], 1)
        self.assertEqual(MobileDevice.objects.get(user=self.user).verified_via, 'password_backup')
        self.assertFalse(StaticToken.objects.filter(token='A1B2C3D4').exists())
        # The same code can't be used twice.
        r2 = self.post('auth/verify/', {'ticket': self.ticket(), 'code': 'A1B2C3D4'})
        self.assertEqual(r2.status_code, 401)

    def test_wrong_code_returns_a_retry_ticket(self):
        first = self.ticket()
        r = self.post('auth/verify/', {'ticket': first, 'code': '000000'})
        self.assertEqual(r.status_code, 401)
        body = r.json()
        self.assertEqual(body['error'], 'invalid_code')
        self.assertEqual(body['attempts_remaining'], 4)
        retry = body['ticket']
        self.assertNotEqual(retry, first)
        # The burned ticket is dead...
        r_old = self.post('auth/verify/', {'ticket': first, 'code': totp_code(self.device)})
        self.assertEqual(r_old.json()['error'], 'invalid_ticket')
        # ...and the retry ticket works, with the original expiry.
        original = MobileAuthTicket.objects.order_by('created_at').first()
        newest = MobileAuthTicket.objects.order_by('-created_at').first()
        self.assertEqual(newest.expires_at, original.expires_at)
        # django-otp throttles a TOTP device for 1s after a failure (web too).
        self.device.refresh_from_db()
        self.device.throttle_reset()
        r2 = self.post('auth/verify/', {'ticket': retry, 'code': totp_code(self.device)})
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertIsNone(cache.get(f'2fa_failures_{self.user.pk}'))

    def test_retries_are_bounded_and_counted(self):
        ticket = self.ticket()
        for i in range(4):
            r = self.post('auth/verify/', {'ticket': ticket, 'code': '000000'})
            ticket = r.json()['ticket']
        r = self.post('auth/verify/', {'ticket': ticket, 'code': '000000'})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {'error': 'invalid_code', 'attempts_remaining': 0})
        # Feeds the web's per-user 2FA failure counter (critical alert at 5).
        self.assertEqual(cache.get(f'2fa_failures_{self.user.pk}'), 5)

    def test_ticket_is_single_use(self):
        ticket = self.ticket()
        self.assertEqual(self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.device)}).status_code, 200)
        r = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.device)})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'invalid_ticket')
        self.assertEqual(MobileDevice.objects.count(), 1)

    def test_expired_ticket(self):
        ticket = self.ticket()
        MobileAuthTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        r = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.device)})
        self.assertEqual(r.json()['error'], 'invalid_ticket')

    def test_quarantined_between_steps(self):
        ticket = self.ticket()
        type(self.user).objects.filter(pk=self.user.pk).update(is_quarantined=True)
        r = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.device)})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(MobileDevice.objects.exists())
