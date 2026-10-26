"""auth/passkey/begin/ and auth/passkey/complete/ (verification mocked)."""
import base64
from types import SimpleNamespace
from unittest.mock import patch

from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings

from src.models.mobile import MobileAuthTicket, MobileDevice
from src.models.webauthn import WebAuthnCredential
from src.tests.mobile.base import MobileTestCase, make_user

CRED_ID = b'\x01\x02mobile-passkey-id'
APK_ORIGIN = 'android:apk-key-hash:abc123'


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


@override_settings(MOBILE_ANDROID_APK_KEY_HASH_ORIGINS=[APK_ORIGIN])
class PasskeyTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('m400')
        self.cred = WebAuthnCredential.objects.create(
            user=self.user, credential_id=CRED_ID, public_key=b'pk', sign_count=3, name='Phone')

    def begin(self):
        r = self.post('auth/passkey/begin/')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def complete(self, challenge_id, cred_id=CRED_ID):
        return self.post('auth/passkey/complete/', {
            'challenge_id': challenge_id,
            'credential': {'id': b64url(cred_id), 'rawId': b64url(cred_id), 'type': 'public-key',
                           'response': {}},
            'platform': 'android', 'device_name': 'Pixel', 'app_version': '1.0',
        })

    def test_begin_returns_a_discoverable_challenge(self):
        body = self.begin()
        self.assertTrue(body['challenge_id'])
        opts = body['options']
        self.assertEqual(opts['userVerification'], 'required')
        self.assertEqual(opts.get('allowCredentials', []), [])   # discoverable
        self.assertTrue(opts['challenge'])
        ticket = MobileAuthTicket.objects.get(purpose=MobileAuthTicket.PURPOSE_PASSKEY_LOGIN)
        self.assertIsNone(ticket.user)
        self.assertEqual(ticket.payload['challenge'], opts['challenge'])
        self.assertNotIn('sessionid', self.api.cookies)

    @patch('webauthn.verify_authentication_response')
    def test_complete_issues_a_device(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=9)
        body = self.begin()
        r = self.complete(body['challenge_id'])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('access_token', r.json())
        kwargs = verify.call_args.kwargs
        self.assertTrue(kwargs['require_user_verification'])
        # Same RP ID / origin the web passkey views compute (SITE_URL, DEBUG off).
        site = urlparse(settings.SITE_URL)
        self.assertEqual(kwargs['expected_rp_id'], site.hostname)
        self.assertEqual(kwargs['expected_origin'], [f'{site.scheme}://{site.netloc}', APK_ORIGIN])
        self.assertIn(APK_ORIGIN, kwargs['expected_origin'])
        self.assertEqual(b64url(kwargs['expected_challenge']), body['options']['challenge'])
        self.assertEqual(kwargs['credential_current_sign_count'], 3)
        self.cred.refresh_from_db()
        self.assertEqual(self.cred.sign_count, 9)
        device = MobileDevice.objects.get(user=self.user)
        self.assertEqual(device.verified_via, 'passkey')
        self.assertEqual(device.platform, 'android')
        # Challenge is single-use.
        self.assertEqual(self.complete(body['challenge_id']).json()['error'], 'invalid_challenge')

    @patch('webauthn.verify_authentication_response', side_effect=Exception('bad signature'))
    def test_failed_verification_is_401_and_counted(self, _verify):
        r = self.complete(self.begin()['challenge_id'])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'passkey_rejected')
        self.assertEqual(cache.get('login_attempts_ip_127.0.0.1'), 1)
        self.assertFalse(MobileDevice.objects.exists())

    @patch('webauthn.verify_authentication_response')
    def test_unknown_credential(self, verify):
        r = self.complete(self.begin()['challenge_id'], cred_id=b'nope')
        self.assertEqual(r.status_code, 401)
        verify.assert_not_called()

    @patch('webauthn.verify_authentication_response')
    def test_quarantined_owner_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=4)
        type(self.user).objects.filter(pk=self.user.pk).update(is_quarantined=True)
        r = self.complete(self.begin()['challenge_id'])
        self.assertEqual(r.status_code, 403)
        self.assertFalse(MobileDevice.objects.exists())

    @patch('webauthn.verify_authentication_response')
    def test_inactive_owner_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=4)
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.complete(self.begin()['challenge_id']).status_code, 403)

    def test_locked_ip_cannot_begin(self):
        cache.set('login_lockout_ip_127.0.0.1', True, 600)
        self.assertEqual(self.post('auth/passkey/begin/').status_code, 423)
