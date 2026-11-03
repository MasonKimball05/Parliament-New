"""
Site admin in the app (v4.4.3): src/mobile/site_admin.py + views_site_admin.py.

The gate matches the website's admin-v2 gate (allowlist, then a password +
secret-key step-up, rate limit and log rows shared with the website), held
per device for 12 hours; every action runs the website view's own helper and
writes the same log rows.

    python manage.py test src.tests.mobile.test_site_admin
"""
import base64
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.mobile.site_admin import UNLOCK_HOURS
from src.models import (
    ActivityLog, AdminActionLog, LoginAlert, LoginLockout, SystemLockdown, TwoFactorRequirement,
)
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.models.webauthn import WebAuthnCredential
from src.models_feature_flags import FeatureFlag, PageToggle
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view import admin_v2

API = '/api/mobile/v1/'
ADMIN = 'sa-admin'
SECRET = 'the-admin-secret-0451'
PW = 'pw-' + ADMIN   # sess_helpers.make_user's password


def admin_routes():
    """(url_name, path) for every Site admin route, with sample arguments."""
    samples = {'flag_id': '1', 'toggle_id': '1', 'user_id': 'nobody', 'device_id':
               '00000000-0000-0000-0000-000000000000', 'lockout_id': '1', 'alert_id': '1'}
    found = []

    def walk(patterns, prefix):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern) and (p.name or '').startswith('mobile_admin_'):
                route = prefix + str(p.pattern)
                for key, value in samples.items():
                    route = route.replace(f'<int:{key}>', value).replace(f'<str:{key}>', value)
                found.append((p.name, '/' + route))

    walk(get_resolver().url_patterns, '')
    return found


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.admin = make_user(ADMIN, is_admin=True, member_type='Officer', email='sa@parliament.test')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.device, self.token, _ = make_device(self.admin)
        self.api = Client()

    def get(self, path, token=None):
        return self.api.get(API + path, **bearer(token or self.token))

    def post(self, path, data=None, token=None):
        return self.api.post(API + path, data or {}, content_type='application/json',
                             **bearer(token or self.token))

    def unlock(self, token=None):
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET}, token=token)
        self.assertEqual(r.status_code, 200, r.content)
        return r

    def code(self, r):
        return r.json().get('error')


# ── The gate ─────────────────────────────────────────────────────────────────

class GateTests(_Base):

    def test_every_admin_route_is_404_for_a_member_not_on_the_allowlist(self):
        member = make_user('sa-member', is_admin=True)   # is_admin alone is not the allowlist
        _, token, _ = make_device(member)
        routes = admin_routes()
        self.assertGreaterEqual(len(routes), 20)
        for name, path in routes:
            for method in (self.api.get, self.api.post):
                r = method(path, **bearer(token))
                self.assertEqual(r.status_code, 404, f'{name} {method.__name__}: {r.status_code}')

    def test_every_route_except_status_unlock_lock_needs_the_unlock(self):
        open_routes = {'mobile_admin_status', 'mobile_admin_unlock', 'mobile_admin_lock',
                       'mobile_admin_unlock_passkey_begin', 'mobile_admin_unlock_passkey_complete'}
        for name, path in admin_routes():
            if name in open_routes:
                continue
            r = self.api.post(path, **bearer(self.token)) if 'revoke' in name or name.endswith(
                ('clear', 'dismiss', 'dismiss_all', 'sign_out', 'release_lockout', 'password_reset',
                 'reset_2fa', '2fa_requirement', 'test_push', 'flag', 'page', 'lockdown')) else \
                self.api.get(path, **bearer(self.token))
            self.assertEqual(r.status_code, 403, f'{name}: {r.status_code}')
            self.assertEqual(self.code(r), 'admin_locked', name)

    def test_signed_out_requests_are_refused_before_anything(self):
        self.assertEqual(self.api.get(API + 'admin/home/').status_code, 401)

    def test_me_says_site_admin_only_for_the_allowlist(self):
        self.assertIs(self.get('me/').json()['site_admin'], True)
        other = make_user('sa-other')
        _, token, _ = make_device(other)
        self.assertIs(self.get('me/', token=token).json()['site_admin'], False)

    def test_status_while_locked(self):
        r = self.get('admin/status/')
        self.assertEqual(r.json(), {'unlocked': False, 'unlocked_until': None, 'has_passkeys': False})


class UnlockTests(_Base):

    def test_unlock_opens_this_device_for_twelve_hours_and_logs_like_the_website(self):
        before = timezone.now()
        r = self.unlock()
        self.assertTrue(r.json()['unlocked'])
        self.device.refresh_from_db()
        self.assertAlmostEqual(
            (self.device.admin_unlocked_until - before).total_seconds(), UNLOCK_HOURS * 3600, delta=30)
        row = ActivityLog.objects.get(action_type='admin_v2_access')
        self.assertEqual(row.user, self.admin)
        self.assertIn('successfully accessed Admin v2 from the app', row.description)
        self.assertEqual(self.get('admin/home/').status_code, 200)

    def test_the_unlock_is_per_device(self):
        self.unlock()
        _, other_token, _ = make_device(self.admin, name='iPad')
        self.assertEqual(self.code(self.get('admin/home/', token=other_token)), 'admin_locked')

    def test_wrong_password_and_wrong_secret(self):
        r = self.post('admin/unlock/', {'password': 'nope', 'secret_key': SECRET})
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_wrong_password'))
        self.assertEqual(r.json()['detail'], 'Invalid user password')
        self.assertFalse(ActivityLog.objects.filter(action_type='security_violation').exists())

        r = self.post('admin/unlock/', {'password': PW, 'secret_key': 'wrong'})
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_wrong_secret'))
        self.assertEqual(r.json()['detail'], 'Invalid secret key')
        self.assertTrue(ActivityLog.objects.filter(
            action_type='security_violation', user=self.admin,
            description__contains='Failed Admin v2 secret key attempt').exists())
        self.assertEqual(cache.get(f'admin_v2_attempts_{self.admin.pk}'), 2)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)

    def test_not_configured(self):
        with mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': ''}):
            r = self.post('admin/unlock/', {'password': PW, 'secret_key': ''})
        self.assertEqual(self.code(r), 'admin_not_configured')

    def test_rate_limit_after_five_failures_even_with_the_right_answers(self):
        for _ in range(5):
            self.post('admin/unlock/', {'password': PW, 'secret_key': 'wrong'})
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.assertEqual(self.code(r), 'admin_rate_limited')
        self.assertIn('Too many failed attempts', r.json()['detail'])

    def test_the_rate_limit_is_shared_with_the_website_both_ways(self):
        web = Client()
        web.force_login(self.admin)
        for _ in range(5):
            web.post(reverse('admin_v2_login'), {'user_password': PW, 'secret_key': 'wrong'})
        self.assertEqual(self.code(self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})),
                         'admin_rate_limited')

        cache.clear()
        for _ in range(5):
            self.post('admin/unlock/', {'password': 'wrong', 'secret_key': SECRET})
        r = web.post(reverse('admin_v2_login'), {'user_password': PW, 'secret_key': SECRET}, follow=True)
        self.assertContains(r, 'Too many failed attempts')
        self.assertFalse(web.session.get('admin_v2_authenticated'))

    def test_success_resets_the_shared_counter(self):
        self.post('admin/unlock/', {'password': 'wrong', 'secret_key': SECRET})
        self.unlock()
        self.assertIsNone(cache.get(f'admin_v2_attempts_{self.admin.pk}'))

    def test_the_secret_is_compared_in_constant_time(self):
        import hmac
        with mock.patch('hmac.compare_digest', wraps=hmac.compare_digest) as cmp:
            self.unlock()
        cmp.assert_called_once_with(SECRET.encode(), SECRET.encode())

    def test_an_expired_unlock_is_locked(self):
        self.unlock()
        MobileDevice.objects.filter(pk=self.device.pk).update(
            admin_unlocked_until=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.code(self.get('admin/home/')), 'admin_locked')
        self.assertFalse(self.get('admin/status/').json()['unlocked'])

    def test_lock_ends_it_and_logs_like_the_website_logout(self):
        self.unlock()
        r = self.post('admin/lock/')
        self.assertEqual(r.json(), {'unlocked': False, 'unlocked_until': None})
        self.assertEqual(self.code(self.get('admin/home/')), 'admin_locked')
        self.assertTrue(ActivityLog.objects.filter(action_type='admin_v2_logout', user=self.admin).exists())

    def test_revoking_the_device_drops_the_unlock(self):
        self.unlock()
        self.device.refresh_from_db()
        self.device.revoke('test')
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)
        self.assertEqual(self.get('admin/home/').status_code, 401)

    def test_signing_out_everywhere_drops_every_unlock(self):
        self.unlock()
        MobileDevice.revoke_all_for(self.admin, 'test')
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)

    def test_leaving_the_allowlist_closes_it_even_while_unlocked(self):
        self.unlock()
        with mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', set()):
            self.assertEqual(self.get('admin/home/').status_code, 404)


class WebLoginUnchangedTests(_Base):
    """The website's step-up runs the shared helper; its pages and rows are as before."""

    def setUp(self):
        super().setUp()
        self.web = Client()
        self.web.force_login(self.admin)

    def test_success(self):
        r = self.web.post(reverse('admin_v2_login'), {'user_password': PW, 'secret_key': SECRET})
        self.assertRedirects(r, reverse('admin_v2_dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.web.session['admin_v2_authenticated'])
        row = ActivityLog.objects.get(action_type='admin_v2_access')
        self.assertEqual(row.description, f'{self.admin.get_display_name()} successfully accessed Admin v2')

    def test_failures_keep_their_messages(self):
        for data, message in (({'user_password': 'x', 'secret_key': SECRET}, 'Invalid user password'),
                              ({'user_password': PW, 'secret_key': 'x'}, 'Invalid secret key')):
            r = self.web.post(reverse('admin_v2_login'), data)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, message)
        self.assertFalse(self.web.session.get('admin_v2_authenticated'))


# ── Passkey unlock (v4.4.3) ──────────────────────────────────────────────────
# Verification is mocked, as in test_passkey.py / test_votes.py.

CRED_ID = b'\x07admin-passkey'


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def assertion(cred_id=CRED_ID):
    return {'id': b64url(cred_id), 'rawId': b64url(cred_id), 'type': 'public-key', 'response': {}}


class PasskeyUnlockTests(_Base):

    def setUp(self):
        super().setUp()
        self.cred = WebAuthnCredential.objects.create(
            user=self.admin, credential_id=CRED_ID, public_key=b'pk', sign_count=1, name='Phone')

    def begin(self, token=None):
        r = self.post('admin/unlock/passkey/begin/', token=token)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def complete(self, challenge_id, cred_id=CRED_ID, token=None):
        return self.post('admin/unlock/passkey/complete/',
                         {'challenge_id': challenge_id, 'credential': assertion(cred_id)}, token=token)

    def test_status_says_whether_to_offer_it(self):
        self.assertIs(self.get('admin/status/').json()['has_passkeys'], True)

    def test_not_on_the_allowlist_is_404_before_anything(self):
        member = make_user('sa-pk-member', is_admin=True)
        WebAuthnCredential.objects.create(user=member, credential_id=b'm', public_key=b'pk', name='M')
        _, token, _ = make_device(member)
        for path in ('admin/unlock/passkey/begin/', 'admin/unlock/passkey/complete/'):
            self.assertEqual(self.post(path, token=token).status_code, 404, path)
        self.assertFalse(MobileAuthTicket.objects.exists())

    def test_begin_asks_for_my_keys_with_user_verification(self):
        data = self.begin()
        self.assertEqual(data['options']['userVerification'], 'required')
        self.assertEqual([c['id'] for c in data['options']['allowCredentials']], [b64url(CRED_ID)])
        ticket = MobileAuthTicket.objects.get(purpose=MobileAuthTicket.PURPOSE_ADMIN_PASSKEY)
        self.assertEqual((ticket.user, ticket.device), (self.admin, self.device))
        self.assertAlmostEqual((ticket.expires_at - timezone.now()).total_seconds(), 120, delta=10)

    @mock.patch('webauthn.verify_authentication_response')
    def test_success_unlocks_only_this_device_and_logs_like_the_website(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=5)
        other_device, other_token, _ = make_device(self.admin, name='iPad')
        before = timezone.now()
        r = self.complete(self.begin()['challenge_id'])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['unlocked'])
        kwargs = verify.call_args.kwargs
        self.assertTrue(kwargs['require_user_verification'])
        self.assertIsInstance(kwargs['expected_origin'], list)   # the site + the Android APK origins
        self.device.refresh_from_db()
        self.assertAlmostEqual(
            (self.device.admin_unlocked_until - before).total_seconds(), UNLOCK_HOURS * 3600, delta=30)
        other_device.refresh_from_db()
        self.assertIsNone(other_device.admin_unlocked_until)
        self.assertEqual(self.get('admin/home/').status_code, 200)
        self.assertEqual(self.code(self.get('admin/home/', token=other_token)), 'admin_locked')
        row = ActivityLog.objects.get(action_type='admin_v2_access')
        self.assertEqual(row.description,
                         f'{self.admin.get_display_name()} accessed Admin v2 via passkey "Phone" from the app')
        self.cred.refresh_from_db()
        self.assertEqual(self.cred.sign_count, 5)

    @mock.patch('webauthn.verify_authentication_response')
    def test_a_challenge_is_single_use(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        challenge = self.begin()['challenge_id']
        self.assertEqual(self.complete(challenge).status_code, 200)
        self.post('admin/lock/')
        r = self.complete(challenge)   # replay
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_bad_challenge'))
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)
        self.assertEqual(cache.get(f'admin_v2_attempts_{self.admin.pk}'), 1)

    @mock.patch('webauthn.verify_authentication_response')
    def test_an_expired_or_unknown_challenge_is_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        challenge = self.begin()['challenge_id']
        MobileAuthTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        for c in (challenge, 'made-up'):
            r = self.complete(c)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_bad_challenge'))
        verify.assert_not_called()
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)

    @mock.patch('webauthn.verify_authentication_response')
    def test_a_challenge_from_another_device_is_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        challenge = self.begin()['challenge_id']
        other_device, other_token, _ = make_device(self.admin, name='iPad')
        r = self.complete(challenge, token=other_token)
        self.assertEqual(self.code(r), 'admin_bad_challenge')
        verify.assert_not_called()
        other_device.refresh_from_db()
        self.assertIsNone(other_device.admin_unlocked_until)

    @mock.patch('webauthn.verify_authentication_response')
    def test_another_users_credential_is_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        other = make_user('sa-pk-other')
        WebAuthnCredential.objects.create(user=other, credential_id=b'theirs', public_key=b'pk', name='Theirs')
        r = self.complete(self.begin()['challenge_id'], cred_id=b'theirs')
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_unknown_credential'))
        verify.assert_not_called()
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)
        self.assertEqual(cache.get(f'admin_v2_attempts_{self.admin.pk}'), 1)

    @mock.patch('webauthn.verify_authentication_response', side_effect=ValueError('bad sig'))
    def test_a_bad_signature_logs_and_counts_toward_the_shared_limit(self, verify):
        for _ in range(5):
            r = self.complete(self.begin()['challenge_id'])
            self.assertEqual(self.code(r), 'admin_failed')
        self.assertEqual(ActivityLog.objects.filter(action_type='security_violation').count(), 5)
        r = self.post('admin/unlock/passkey/begin/')
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_rate_limited'))
        # The same counter as the password step and the website.
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.assertEqual(self.code(r), 'admin_rate_limited')
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)

    def test_bad_input(self):
        r = self.post('admin/unlock/passkey/complete/', {'challenge_id': 5})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))

    def test_no_passkeys(self):
        self.cred.delete()
        _, token, _ = make_device(self.admin, name='fresh')   # deleting a passkey may sign devices out
        r = self.post('admin/unlock/passkey/begin/', token=token)
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_no_passkeys'))


class WebPasskeyUnchangedTests(_Base):
    """The website's Admin v2 passkey step runs the shared helpers; its responses and rows are as before."""

    def setUp(self):
        super().setUp()
        WebAuthnCredential.objects.create(user=self.admin, credential_id=CRED_ID, public_key=b'pk',
                                          sign_count=1, name='Laptop')
        self.web = Client()
        self.web.force_login(self.admin)

    def web_complete(self, cred_id=CRED_ID):
        return self.web.post(reverse('admin_v2_passkey_auth_complete'), json.dumps(assertion(cred_id)),
                             content_type='application/json')

    @mock.patch('webauthn.verify_authentication_response')
    def test_success(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        r = self.web.post(reverse('admin_v2_passkey_auth_begin'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['userVerification'], 'required')
        r = self.web_complete()
        self.assertEqual(r.json(), {'ok': True, 'redirect': reverse('admin_v2_dashboard')})
        self.assertTrue(self.web.session['admin_v2_authenticated'])
        self.assertEqual(verify.call_args.kwargs['expected_origin'].__class__, str)
        # v4.4.3: the website now holds the passkey to the user verification it asks for
        self.assertIs(verify.call_args.kwargs['require_user_verification'], True)
        row = ActivityLog.objects.get(action_type='admin_v2_access')
        self.assertEqual(row.description, f'{self.admin.get_display_name()} accessed Admin v2 via passkey "Laptop"')
        # The web's own session never unlocks a phone.
        self.device.refresh_from_db()
        self.assertIsNone(self.device.admin_unlocked_until)

    def test_no_challenge(self):
        r = self.web_complete()
        self.assertEqual((r.status_code, r.json()), (400, {'error': 'No authentication in progress.'}))

    @mock.patch('webauthn.verify_authentication_response')
    def test_failures_keep_their_messages(self, verify):
        verify.side_effect = ValueError('bad sig')
        self.web.post(reverse('admin_v2_passkey_auth_begin'))
        r = self.web_complete(cred_id=b'not-mine')
        self.assertEqual((r.status_code, r.json()), (400, {'error': 'Unknown or unauthorized credential.'}))
        self.web.post(reverse('admin_v2_passkey_auth_begin'))
        r = self.web_complete()
        self.assertEqual((r.status_code, r.json()), (400, {'error': 'Passkey verification failed.'}))
        row = ActivityLog.objects.get(action_type='security_violation')
        self.assertEqual(row.description, f'Failed Admin v2 passkey attempt by {self.admin.get_display_name()}')
        self.web.post(reverse('admin_v2_passkey_auth_begin'))
        r = self.web.post(reverse('admin_v2_passkey_auth_complete'), 'not json', content_type='application/json')
        self.assertEqual((r.status_code, r.json()), (400, {'error': 'Invalid request body.'}))
        self.assertFalse(self.web.session.get('admin_v2_authenticated'))

    def test_rate_limit_is_shared(self):
        cache.set(f'admin_v2_attempts_{self.admin.pk}', 5, 900)
        r = self.web.post(reverse('admin_v2_passkey_auth_begin'))
        self.assertEqual((r.status_code, r.json()),
                         (429, {'error': 'Too many failed attempts. Try again in 15 minutes.'}))


# ── Actions ──────────────────────────────────────────────────────────────────

class _Unlocked(_Base):
    def setUp(self):
        super().setUp()
        self.unlock()
        ActivityLog.objects.all().delete()


class HomeTests(_Unlocked):

    def setUp(self):
        super().setUp()
        FeatureFlag.objects.get_or_create(name='maintenance_mode', defaults={
            'display_name': 'Maintenance mode', 'description': 'x', 'is_enabled': False})
        # The dashboard's Celery card pings the broker (1 s timeout); not under test here.
        celery = mock.patch.object(admin_v2, 'dashboard_celery_summary', return_value={
            'workers_up': True, 'task_count': 3, 'stale_count': 0, 'disabled_count': 0})
        celery.start()
        self.addCleanup(celery.stop)

    def test_home(self):
        r = self.get('admin/home/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['unlocked'])
        for key in ('stats', 'celery', 'attention', 'maintenance', 'lockdown', 'flags', 'pages'):
            self.assertIn(key, body)
        self.assertFalse(any(f['name'] == 'maintenance_mode' for f in body['flags']))
        self.assertIsNotNone(body['maintenance'])
        self.assertTrue(body['lockdown']['can_manage'])

    def test_flag_switch_matches_the_website_and_ignores_a_stale_request(self):
        flag = FeatureFlag.objects.create(name='sa_test_flag', display_name='SA test', description='x',
                                          is_enabled=True)
        r = self.post(f'admin/flags/{flag.pk}/', {'enabled': False})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['changed'])
        flag.refresh_from_db()
        self.assertFalse(flag.is_enabled)
        row = ActivityLog.objects.get(action_type='feature_flag_toggle')
        self.assertEqual(row.description, f'{self.admin.get_display_name()} disabled feature: SA test')

        r = self.post(f'admin/flags/{flag.pk}/', {'enabled': False})
        self.assertFalse(r.json()['changed'])
        self.assertEqual(ActivityLog.objects.filter(action_type='feature_flag_toggle').count(), 1)
        self.assertEqual(self.code(self.post(f'admin/flags/{flag.pk}/', {'enabled': 'no'})), 'invalid_input')

    def test_maintenance_mode_is_its_flag(self):
        maintenance = self.get('admin/home/').json()['maintenance']
        wanted = not maintenance['is_enabled']
        r = self.post(f'admin/flags/{maintenance["id"]}/', {'enabled': wanted})
        self.assertEqual(r.json()['flag']['is_enabled'], wanted)
        # Admins still get through maintenance mode.
        self.assertEqual(self.get('admin/home/').json()['maintenance']['is_enabled'], wanted)

    def test_page_switch_logs_like_the_website(self):
        toggle = PageToggle.objects.create(url_name='sa_test_page', display_name='SA page', is_enabled=True)
        self.post(f'admin/pages/{toggle.pk}/', {'enabled': False})
        toggle.refresh_from_db()
        self.assertFalse(toggle.is_enabled)
        row = ActivityLog.objects.get(action_type='page_toggle')
        self.assertEqual(row.description, f'{self.admin.get_display_name()} disabled page: SA page')

    def test_lockdown_on_and_off_through_the_website_helpers(self):
        with mock.patch('src.security_notifications.alert_lockdown_activated') as on, \
                mock.patch('src.security_notifications.alert_lockdown_deactivated') as off:
            r = self.post('admin/lockdown/', {'action': 'activate', 'reason': 'Drill',
                                              'whitelisted_ips': ['127.0.0.1']})
            self.assertEqual(r.status_code, 200, r.content)
            lockdown = SystemLockdown.get_instance()
            self.assertTrue(lockdown.is_active)
            self.assertEqual(lockdown.whitelisted_ips, ['127.0.0.1'])
            on.assert_called_once_with(self.admin, 'Drill')
            r = self.post('admin/lockdown/', {'action': 'deactivate'})
            self.assertEqual(r.status_code, 200, r.content)
            self.assertFalse(SystemLockdown.get_instance().is_active)
            off.assert_called_once_with(self.admin)

    def test_lockdown_needs_is_admin_as_on_the_website(self):
        self.admin.is_admin = False
        self.admin.save(update_fields=['is_admin'])
        r = self.post('admin/lockdown/', {'action': 'activate'})
        self.assertEqual((r.status_code, self.code(r)), (403, 'admin_required'))
        self.assertFalse(SystemLockdown.get_instance().is_active)


class MemberTests(_Unlocked):

    def setUp(self):
        super().setUp()
        self.member = make_user('sa-target', name='Target Member', email='target@parliament.test')
        self.member_device, self.member_token, _ = make_device(self.member)

    def test_search_and_member_page(self):
        r = self.get('admin/members/?q=Target')
        self.assertEqual([m['user_id'] for m in r.json()['members']], ['sa-target'])
        r = self.get('admin/members/sa-target/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['member']['name'], 'Target Member')
        device = body['devices'][0]
        self.assertNotIn('push_token', device)
        self.assertEqual(self.get('admin/members/nobody/').status_code, 404)

    def test_member_activity_excludes_kai_rows_like_the_member_page(self):
        ActivityLog.log_activity(action_type='kai_action', user=self.member,
                                 description='Target Member submitted Kai case #4')
        ActivityLog.log_activity(action_type='profile_updated', user=self.member, description='Updated profile')
        descriptions = [a['description'] for a in self.get('admin/members/sa-target/').json()['activity']]
        self.assertIn('Updated profile', descriptions)
        self.assertFalse(any('Kai' in d for d in descriptions))

    def test_security_page(self):
        r = self.get('admin/members/sa-target/security/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('stats', r.json())

    def test_sign_out_everywhere(self):
        r = self.post('admin/members/sa-target/sign-out/')
        self.assertEqual(r.status_code, 200, r.content)
        self.member_device.refresh_from_db()
        self.assertIsNotNone(self.member_device.revoked_at)
        row = ActivityLog.objects.get(action_type='user_edited')
        self.assertIn('signed Target Member out everywhere', row.description)

    def test_sign_yourself_out_keeps_this_phone(self):
        other, _, _ = make_device(self.admin, name='Old phone')
        self.post(f'admin/members/{ADMIN}/sign-out/')
        other.refresh_from_db()
        self.device.refresh_from_db()
        self.assertIsNotNone(other.revoked_at)
        self.assertIsNone(self.device.revoked_at)
        self.assertIsNotNone(self.device.admin_unlocked_until)

    def test_revoke_one_device(self):
        r = self.post(f'admin/members/sa-target/devices/{self.member_device.public_id}/revoke/')
        self.assertTrue(r.json()['revoked'])
        self.member_device.refresh_from_db()
        self.assertIsNotNone(self.member_device.revoked_at)
        self.assertIn("signed out Target Member's iOS app",
                      ActivityLog.objects.get(action_type='user_edited').description)
        # Another member's device id under this member is a 404.
        self.assertEqual(self.post(f'admin/members/sa-target/devices/{self.device.public_id}/revoke/')
                         .status_code, 404)
        self.assertEqual(self.post('admin/members/sa-target/devices/not-a-uuid/revoke/').status_code, 404)

    def test_release_lockout_writes_the_audit_row(self):
        LoginLockout.objects.create(username=self.member.username, ip_address='10.1.1.1',
                                    expires_at=timezone.now() + timedelta(minutes=10))
        r = self.post('admin/members/sa-target/release-lockout/')
        self.assertEqual(r.json()['lockout_rows'], 1)
        self.assertTrue(AdminActionLog.objects.filter(action='account_unlocked', target_user=self.member).exists())

    def test_password_reset_random_must_be_emailed_and_never_comes_back(self):
        old_hash = self.member.password
        r = self.post('admin/members/sa-target/password-reset/', {'password_type': 'random'})
        self.assertEqual(self.code(r), 'invalid_input')
        self.member.refresh_from_db()
        self.assertEqual(self.member.password, old_hash)

        r = self.post('admin/members/sa-target/password-reset/',
                      {'password_type': 'random', 'send_email': True, 'reason': 'Phone stolen'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'ok': True, 'email_sent': True, 'email_failed': False})
        self.assertEqual(len(mail.outbox), 1)
        alert = LoginAlert.objects.get(user=self.member, title='Password Reset by Administrator')
        new_password = alert.resolution_notes.removeprefix('New password: ')
        self.assertNotIn(new_password, r.content.decode())
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(new_password))
        self.assertTrue(ActivityLog.objects.filter(action_type='forced_password_reset').exists())
        # The member's devices end with the old password.
        self.assertEqual(self.get('me/', token=self.member_token).status_code, 401)
        # The alert's note (the password) is withheld from the app.
        alerts = self.get('admin/members/sa-target/security/').json()['alerts']
        self.assertIsNone(alerts[0]['notes'])
        self.assertNotIn(new_password, str(self.get('admin/alerts/').json()))

    def test_password_reset_custom_and_rules(self):
        r = self.post('admin/members/sa-target/password-reset/',
                      {'password_type': 'custom', 'custom_password': 'short'})
        self.assertEqual(r.json()['detail'], 'Custom password should be at least 9 characters')
        r = self.post('admin/members/sa-target/password-reset/',
                      {'password_type': 'custom', 'custom_password': 'long-enough-pass-1'})
        self.assertEqual(r.json()['email_sent'], False)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('long-enough-pass-1'))
        r = self.post(f'admin/members/{ADMIN}/password-reset/',
                      {'password_type': 'custom', 'custom_password': 'long-enough-pass-1'})
        self.assertEqual(self.code(r), 'invalid_input')

    def test_reset_two_factor(self):
        TOTPDevice.objects.create(user=self.member, name='default', confirmed=True)
        r = self.post('admin/members/sa-target/reset-2fa/')
        self.assertEqual(r.json()['removed'], 1)
        self.assertFalse(TOTPDevice.objects.filter(user=self.member).exists())
        self.assertTrue(ActivityLog.objects.filter(action_type='security_violation',
                                                   description=f'Admin reset 2FA for {self.member.name}').exists())

    def test_two_factor_requirement(self):
        r = self.post('admin/members/sa-target/2fa-requirement/', {'requirement': 'required', 'reason': 'Officer'})
        self.assertEqual(r.json()['two_factor']['requirement'], 'required')
        self.assertEqual(TwoFactorRequirement.objects.get(user=self.member).requirement, 'required')
        self.assertTrue(ActivityLog.objects.filter(
            action_type='other', description=f'Set 2FA requirement to required for {self.member.name}').exists())
        r = self.post('admin/members/sa-target/2fa-requirement/', {'requirement': 'clear'})
        self.assertEqual(r.json()['two_factor']['requirement'], 'policy')
        self.assertEqual(self.code(self.post('admin/members/sa-target/2fa-requirement/',
                                             {'requirement': 'bogus'})), 'invalid_input')


class SecurityTests(_Unlocked):

    def test_lockouts_and_clear(self):
        lockout = LoginLockout.objects.create(username='someone', ip_address='10.2.2.2',
                                              expires_at=timezone.now() + timedelta(minutes=10))
        body = self.get('admin/lockouts/').json()
        self.assertEqual(body['active_count'], 1)
        r = self.post(f'admin/lockouts/{lockout.pk}/clear/')
        self.assertEqual(r.json()['lockout_rows'], 1)
        lockout.refresh_from_db()
        self.assertTrue(lockout.is_cleared)

    def test_alerts_dismiss_and_dismiss_all(self):
        member = make_user('sa-alerted')
        a1 = LoginAlert.objects.create(user=member, alert_type='other', severity='high', title='One',
                                       description='x')
        LoginAlert.objects.create(user=member, alert_type='other', severity='critical', title='Two',
                                  description='y')
        body = self.get('admin/alerts/').json()
        self.assertEqual(body['stats']['new'], 2)
        r = self.post(f'admin/alerts/{a1.pk}/dismiss/')
        self.assertEqual(r.json()['new_count'], 1)
        a1.refresh_from_db()
        self.assertEqual(a1.status, 'resolved')
        r = self.post('admin/alerts/dismiss-all/')
        self.assertEqual(r.json()['dismissed'], 1)
        self.assertTrue(ActivityLog.objects.filter(action_type='security',
                                                   description__contains='bulk-dismissed 1').exists())

    def test_audit_log_is_paged_and_admin_only(self):
        from src.models import log_admin_action
        for i in range(35):
            log_admin_action(actor=self.admin, action='other', detail=f'entry {i}')
        body = self.get('admin/audit-log/').json()
        self.assertEqual((body['total'], len(body['entries']), body['has_next']), (35, 30, True))
        self.assertIn('actions', body)
        self.assertEqual(len(self.get('admin/audit-log/?page=2').json()['entries']), 5)
        self.admin.is_admin = False
        self.admin.save(update_fields=['is_admin'])
        self.assertEqual(self.code(self.get('admin/audit-log/')), 'admin_required')


class AppInstallsTests(_Unlocked):

    @override_settings(EXPO_ACCESS_TOKEN='very-secret-expo-token')
    def test_overview_sends_booleans_not_settings(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(push_token='ExponentPushToken[secret-push]')
        r = self.get('admin/app-installs/')
        self.assertEqual(r.status_code, 200, r.content)
        text = r.content.decode()
        self.assertNotIn('very-secret-expo-token', text)
        self.assertNotIn('secret-push', text)
        self.assertNotIn('EXPO_ACCESS_TOKEN', text)
        setup = r.json()['setup']
        self.assertTrue(all(set(row) == {'label', 'ok'} for row in setup))

    def test_test_push(self):
        member = make_user('sa-push')
        with mock.patch('src.mobile.push.live_push_devices', return_value=['d1', 'd2']), \
                mock.patch('src.mobile.push.send_to_devices', return_value=2):
            r = self.post('admin/app-installs/test-push/', {'user_id': 'sa-push'})
        self.assertEqual(r.json(), {'outcome': 'accepted', 'accepted': 2, 'devices': 2})
        self.assertTrue(ActivityLog.objects.filter(
            action_type='user_edited', description__contains=f'sent a test push to {member.get_display_name()}'
        ).exists())
        with mock.patch('src.mobile.push.live_push_devices', return_value=[]):
            r = self.post('admin/app-installs/test-push/', {'user_id': 'sa-push'})
        self.assertEqual(r.json()['outcome'], 'no_devices')


class WebPageToggleTests(_Base):
    """v4.4.3: the dashboard's in-place page switch now writes its log row too."""

    def test_xhr_page_toggle_logs(self):
        web = Client()
        web.force_login(self.admin)
        session = web.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        toggle = PageToggle.objects.create(url_name='sa_xhr_page', display_name='XHR page', is_enabled=True)
        r = web.post(reverse('toggle_page', args=[toggle.pk]), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json(), {'is_enabled': False, 'status': 'disabled'})
        self.assertTrue(ActivityLog.objects.filter(action_type='page_toggle').exists())
