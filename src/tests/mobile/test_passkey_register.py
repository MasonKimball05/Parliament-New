"""
Your passkeys in the app (v4.4.3, src/mobile/views_passkeys.py): list,
register begin / complete, rename, delete — through the website's code
(src/view/webauthn.py), with its password rule, log lines and notifications.
py_webauthn's verification is mocked (no authenticator in a test).

    python manage.py test src.tests.mobile.test_passkey_register
"""
import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import Notification
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.models.webauthn import WebAuthnCredential
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'
APK_ORIGIN = 'android:apk-key-hash:pkr123'
NEW_ID = b'\x09new-app-passkey'
REG = 'src.view.webauthn.webauthn.verify_registration_response'


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def verified(cred_id=NEW_ID):
    return SimpleNamespace(credential_id=cred_id, credential_public_key=b'pub', sign_count=0, aaguid='')


def credential(cred_id=NEW_ID):
    return {'id': b64url(cred_id), 'rawId': b64url(cred_id), 'type': 'public-key',
            'response': {'clientDataJSON': 'e30', 'attestationObject': 'AA'}}


@override_settings(MOBILE_ANDROID_APK_KEY_HASH_ORIGINS=[APK_ORIGIN])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = make_user('pkr-user', email='pkr@example.com')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, url, access=None):
        return self.api.get(API + url, **bearer(access or self.access))

    def post(self, url, body=None, access=None):
        return self.api.post(API + url, body or {}, content_type='application/json',
                             **bearer(access or self.access))

    def begin(self, password='pw-pkr-user', access=None):
        return self.post('account/passkeys/register/begin/', {'password': password}, access=access)

    def complete(self, challenge_id, cred_id=NEW_ID, name='My iPhone', access=None):
        return self.post('account/passkeys/register/complete/', {
            'challenge_id': challenge_id, 'credential': credential(cred_id), 'name': name}, access=access)


class AuthAndListTests(_Base):

    def test_every_endpoint_needs_a_signed_in_install(self):
        cred = WebAuthnCredential.objects.create(user=self.user, credential_id=b'x', public_key=b'p')
        for method, url in [('get', 'account/passkeys/'),
                            ('post', 'account/passkeys/register/begin/'),
                            ('post', 'account/passkeys/register/complete/'),
                            ('post', f'account/passkeys/{cred.pk}/rename/'),
                            ('post', f'account/passkeys/{cred.pk}/delete/')]:
            r = getattr(self.api, method)(API + url)
            self.assertEqual(r.status_code, 401, url)

    def test_a_browser_session_is_not_enough(self):
        self.api.force_login(self.user)
        self.assertEqual(self.api.get(API + 'account/passkeys/').status_code, 401)

    def test_list_is_mine_without_key_material(self):
        used = timezone.now() - timedelta(days=1)
        WebAuthnCredential.objects.create(user=self.user, credential_id=b'a', public_key=b'secret',
                                          name='Laptop', last_used_at=used)
        WebAuthnCredential.objects.create(user=make_user('pkr-other'), credential_id=b'b', public_key=b'p')
        r = self.get('account/passkeys/')
        self.assertEqual(r.status_code, 200, r.content)
        rows = r.json()['passkeys']
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]), {'id', 'name', 'created_at', 'last_used_at'})
        self.assertEqual(rows[0]['name'], 'Laptop')
        self.assertTrue(rows[0]['last_used_at'])

    def test_me_says_whether_there_are_passkeys(self):
        self.assertIs(self.get('me/').json()['has_passkeys'], False)
        WebAuthnCredential.objects.create(user=self.user, credential_id=b'a', public_key=b'p')
        self.assertIs(self.get('me/').json()['has_passkeys'], True)


class ReauthRuleTests(_Base):
    """The website asks for the password before adding a passkey; so does the app."""

    def test_begin_without_password_is_refused(self):
        r = self.post('account/passkeys/register/begin/', {})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertFalse(MobileAuthTicket.objects.exists())

    def test_begin_with_wrong_password_is_refused(self):
        r = self.begin('nope')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertEqual(cache.get(f'cred_change_attempts_{self.user.pk}'), 1)

    def test_shared_failure_budget_with_the_website(self):
        # Four wrong on the website, one in the app: the fifth locks both.
        web = Client()
        web.force_login(self.user)
        with patch('src.middleware.two_factor.Enforce2FAMiddleware.user_requires_2fa', return_value=False):
            for _ in range(4):
                web.post(reverse('passkey_register_begin'), {'password': 'nope'})
        self.begin('nope')
        r = self.begin()
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'reauth_locked')
        self.assertIn('Too many', r.json()['detail'])
        cred = WebAuthnCredential.objects.create(user=self.user, credential_id=b'z', public_key=b'p')
        r = self.post(f'account/passkeys/{cred.pk}/delete/', {'password': 'pw-pkr-user'})
        self.assertEqual(r.status_code, 429)
        self.assertTrue(WebAuthnCredential.objects.filter(pk=cred.pk).exists())


class RegisterTests(_Base):

    def test_begin_returns_the_websites_options_and_a_bound_ticket(self):
        WebAuthnCredential.objects.create(user=self.user, credential_id=b'old-key', public_key=b'p')
        r = self.begin()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        opts = body['options']
        self.assertEqual(opts['authenticatorSelection']['residentKey'], 'preferred')
        self.assertEqual(opts['authenticatorSelection']['userVerification'], 'required')
        self.assertEqual([c['id'] for c in opts['excludeCredentials']], [b64url(b'old-key')])
        self.assertEqual(opts['rp']['id'], urlparse(settings.SITE_URL).hostname)
        self.assertEqual(opts['user']['id'], b64url(str(self.user.pk).encode()))
        ticket = MobileAuthTicket.objects.get(purpose=MobileAuthTicket.PURPOSE_PASSKEY_REGISTER)
        self.assertEqual(ticket.user, self.user)
        self.assertEqual(ticket.device, self.device)
        self.assertEqual(ticket.payload['challenge'], opts['challenge'])
        self.assertLessEqual(ticket.expires_at - timezone.now(), timedelta(seconds=180))

    @patch(REG)
    def test_complete_creates_a_credential_for_this_member(self, verify):
        verify.return_value = verified()
        body = self.begin().json()
        r = self.complete(body['challenge_id'])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['passkey']['name'], 'My iPhone')
        cred = WebAuthnCredential.objects.get(credential_id=NEW_ID)
        self.assertEqual(cred.user, self.user)
        self.assertEqual(bytes(cred.public_key), b'pub')
        kwargs = verify.call_args.kwargs
        self.assertTrue(kwargs['require_user_verification'])
        site = urlparse(settings.SITE_URL)
        self.assertEqual(kwargs['expected_rp_id'], site.hostname)
        self.assertEqual(kwargs['expected_origin'], [f'{site.scheme}://{site.netloc}', APK_ORIGIN])
        self.assertEqual(b64url(kwargs['expected_challenge']), body['options']['challenge'])
        self.assertEqual(json.loads(kwargs['credential'])['rawId'], b64url(NEW_ID))

    @patch(REG)
    def test_added_notification_and_email_match_the_website(self, verify):
        verify.return_value = verified()
        with self.assertLogs('security', 'INFO') as logs:
            self.complete(self.begin().json()['challenge_id'])
        self.assertTrue(any('Passkey registered for pkr-user: "My iPhone"' in line and 'from the app' in line
                            for line in logs.output))
        n = Notification.objects.get(recipient=self.user, notification_type='security')
        self.assertEqual(n.title, 'A new passkey was added to your account')
        self.assertIn('"My iPhone"', n.message)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('A new passkey was added', mail.outbox[0].subject)

    @patch(REG)
    def test_blank_name_becomes_passkey(self, verify):
        verify.return_value = verified()
        r = self.complete(self.begin().json()['challenge_id'], name='   ')
        self.assertEqual(r.json()['passkey']['name'], 'Passkey')

    @patch(REG)
    def test_replayed_challenge_refused(self, verify):
        verify.return_value = verified()
        cid = self.begin().json()['challenge_id']
        self.assertEqual(self.complete(cid).status_code, 200)
        verify.return_value = verified(b'another')
        r = self.complete(cid, cred_id=b'another')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid_challenge')
        self.assertFalse(WebAuthnCredential.objects.filter(credential_id=b'another').exists())

    @patch(REG)
    def test_expired_challenge_refused(self, verify):
        verify.return_value = verified()
        cid = self.begin().json()['challenge_id']
        MobileAuthTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        r = self.complete(cid)
        self.assertEqual(r.json()['error'], 'invalid_challenge')
        verify.assert_not_called()

    @patch(REG)
    def test_challenge_from_another_install_refused(self, verify):
        verify.return_value = verified()
        cid = self.begin().json()['challenge_id']
        _, other_access, _ = make_device(self.user, name='iPad')
        r = self.complete(cid, access=other_access)
        self.assertEqual(r.json()['error'], 'invalid_challenge')
        self.assertFalse(WebAuthnCredential.objects.exists())

    @patch(REG)
    def test_challenge_for_another_member_refused(self, verify):
        verify.return_value = verified()
        other = make_user('pkr-two')
        _, other_access, _ = make_device(other)
        cid = self.begin('pw-pkr-two', access=other_access).json()['challenge_id']
        r = self.complete(cid)   # this member, their challenge
        self.assertEqual(r.json()['error'], 'invalid_challenge')

    @patch(REG, side_effect=Exception('bad attestation'))
    def test_failed_verification(self, _verify):
        r = self.complete(self.begin().json()['challenge_id'])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'passkey_rejected')
        self.assertFalse(WebAuthnCredential.objects.exists())
        self.assertEqual(Notification.objects.count(), 0)

    @patch(REG)
    def test_already_registered_credential(self, verify):
        WebAuthnCredential.objects.create(user=self.user, credential_id=NEW_ID, public_key=b'p')
        verify.return_value = verified()
        r = self.complete(self.begin().json()['challenge_id'])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'already_registered')
        self.assertEqual(WebAuthnCredential.objects.count(), 1)

    def test_malformed_complete(self):
        r = self.post('account/passkeys/register/complete/', {'challenge_id': 'x'})
        self.assertEqual(r.json()['error'], 'invalid_input')


class RenameDeleteTests(_Base):

    def setUp(self):
        super().setUp()
        self.cred = WebAuthnCredential.objects.create(
            user=self.user, credential_id=b'mine', public_key=b'p', name='Old')
        self.other = make_user('pkr-other')
        self.theirs = WebAuthnCredential.objects.create(
            user=self.other, credential_id=b'theirs', public_key=b'p', name='Theirs')

    def test_rename_own(self):
        r = self.post(f'account/passkeys/{self.cred.pk}/rename/', {'name': '  Work phone  '})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['passkey']['name'], 'Work phone')
        self.cred.refresh_from_db()
        self.assertEqual(self.cred.name, 'Work phone')

    def test_rename_empty_refused(self):
        r = self.post(f'account/passkeys/{self.cred.pk}/rename/', {'name': ' '})
        self.assertEqual(r.json()['error'], 'invalid_input')

    def test_rename_another_members_is_404(self):
        r = self.post(f'account/passkeys/{self.theirs.pk}/rename/', {'name': 'Mine now'})
        self.assertEqual(r.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.name, 'Theirs')

    def test_delete_needs_the_password(self):
        r = self.post(f'account/passkeys/{self.cred.pk}/delete/', {'password': 'nope'})
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertTrue(WebAuthnCredential.objects.filter(pk=self.cred.pk).exists())

    def test_delete_another_members_is_404(self):
        r = self.post(f'account/passkeys/{self.theirs.pk}/delete/', {'password': 'pw-pkr-user'})
        self.assertEqual(r.status_code, 404)
        self.assertTrue(WebAuthnCredential.objects.filter(pk=self.theirs.pk).exists())
        self.assertIsNone(cache.get(f'cred_change_attempts_{self.user.pk}'))

    def test_delete_own_notifies_and_keeps_this_password_install(self):
        other_device, _, _ = make_device(self.user, name='iPad')
        with self.assertLogs('security', 'INFO') as logs:
            r = self.post(f'account/passkeys/{self.cred.pk}/delete/', {'password': 'pw-pkr-user'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json(), {'ok': True, 'signed_out': False})
        self.assertFalse(WebAuthnCredential.objects.filter(pk=self.cred.pk).exists())
        self.assertTrue(any('Passkey "Old" deleted by pkr-user' in line for line in logs.output))
        n = Notification.objects.get(recipient=self.user, notification_type='security')
        self.assertEqual(n.title, 'A passkey was removed from your account')
        self.assertIn('removed from your Parliament account', mail.outbox[0].body)
        self.device.refresh_from_db()
        other_device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)
        self.assertIsNotNone(other_device.revoked_at)   # every other install, as on the website

    def test_delete_from_a_passkey_install_signs_it_out_too(self):
        device, access, _ = make_device(self.user, verified_via='passkey')
        r = self.post(f'account/passkeys/{self.cred.pk}/delete/', {'password': 'pw-pkr-user'}, access=access)
        self.assertEqual(r.json(), {'ok': True, 'signed_out': True})
        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)


class WebFlowUnchangedTests(TestCase):
    """The website's own passkey views, after their code moved into helpers."""

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = make_user('pkr-web', email='web@example.com')
        self.web = Client()
        self.web.force_login(self.user)
        p = patch('src.middleware.two_factor.Enforce2FAMiddleware.user_requires_2fa', return_value=False)
        p.start()
        self.addCleanup(p.stop)

    @patch(REG)
    def test_register_rename_delete(self, verify):
        verify.return_value = verified()
        device, _, _ = make_device(self.user)
        r = self.web.post(reverse('passkey_register_begin'), {'password': 'pw-pkr-web'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['authenticatorSelection']['residentKey'], 'preferred')
        r = self.web.post(reverse('passkey_register_complete'), json.dumps(credential()),
                          content_type='application/json', HTTP_X_PASSKEY_NAME='Laptop')
        self.assertEqual(r.json(), {'ok': True, 'name': 'Laptop'})
        self.assertFalse(verify.call_args.kwargs['require_user_verification'])   # as before
        self.assertIsInstance(verify.call_args.kwargs['expected_origin'], str)
        cred = WebAuthnCredential.objects.get(user=self.user)
        self.assertEqual(mail.outbox[-1].subject, '[Parliament] A new passkey was added to your account')

        r = self.web.post(reverse('passkey_rename', args=[cred.pk]), json.dumps({'name': 'Desk'}),
                          content_type='application/json')
        self.assertEqual(r.json(), {'ok': True, 'name': 'Desk'})

        r = self.web.post(reverse('passkey_delete', args=[cred.pk]), {'password': 'pw-pkr-web'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(WebAuthnCredential.objects.exists())
        self.assertEqual(mail.outbox[-1].subject, '[Parliament] A passkey was removed from your account')
        device.refresh_from_db()
        self.assertIsNotNone(device.revoked_at)   # the website still signs every app install out
        self.assertEqual(MobileDevice.objects.filter(revoked_at__isnull=True).count(), 0)
