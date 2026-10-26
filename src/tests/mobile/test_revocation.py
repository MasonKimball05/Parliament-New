"""
Mobile plan, Phase 1 — when app devices are signed out.

Explicit hooks (``src/mobile/revocation.py``): a second factor deleted (TOTP,
backup codes, passkey — including via 2FA self-service recovery) and "sign out
everywhere" (``UserSession.revoke_other_sessions``). Checked live: password
change (``auth_fingerprint``), quarantine, deactivation, Removed.
"""
from django.test import Client, TestCase, override_settings
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import UserSession
from src.models.mobile import MobileDevice
from src.models.webauthn import WebAuthnCredential
from src.tests.mobile.sess_helpers import bearer, make_device, make_user


def _live(device):
    device.refresh_from_db()
    return device.still_allowed()[0]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class SecondFactorRemovalRevokesTests(TestCase):

    def setUp(self):
        self.user = make_user('rev-1')
        self.other = make_user('rev-2')
        self.device, self.access, _ = make_device(self.user, 'password_totp')
        self.second, _, _ = make_device(self.user, 'password_totp', name='Tablet')
        self.bystander, _, _ = make_device(self.other)

    def _assert_all_revoked(self, reason_fragment):
        for d in (self.device, self.second):
            self.assertFalse(_live(d))
            self.assertIn(reason_fragment, d.revoked_reason)
            self.assertEqual(d.push_token, '')
        self.assertTrue(_live(self.bystander))

    def test_totp_device_deleted(self):
        TOTPDevice.objects.create(user=self.user, name='a', confirmed=True).delete()
        self._assert_all_revoked('authenticator')

    def test_totp_queryset_delete(self):
        TOTPDevice.objects.create(user=self.user, name='a', confirmed=True)
        TOTPDevice.objects.filter(user=self.user).delete()
        self._assert_all_revoked('authenticator')

    def test_unconfirmed_totp_device_deleted_is_not_a_security_event(self):
        TOTPDevice.objects.create(user=self.user, name='half-setup', confirmed=False).delete()
        self.assertTrue(_live(self.device))

    def test_backup_codes_deleted(self):
        StaticDevice.objects.create(user=self.user, name='backup', confirmed=True).delete()
        self._assert_all_revoked('backup')

    def test_passkey_deleted(self):
        WebAuthnCredential.objects.create(
            user=self.user, credential_id=b'cred-1', public_key=b'pk').delete()
        self._assert_all_revoked('passkey')

    def test_revoked_token_no_longer_authenticates(self):
        TOTPDevice.objects.create(user=self.user, name='a', confirmed=True).delete()
        r = Client().get('/api/mobile/v1/devices/', **bearer(self.access))
        self.assertEqual(r.status_code, 401)

    def test_two_factor_recovery_revokes(self):
        from src.view.two_factor_recovery import _recovery_token
        TOTPDevice.objects.create(user=self.user, name='lost', confirmed=True)
        StaticDevice.objects.create(user=self.user, name='backup', confirmed=True)
        # The recovery deletes devices, so creating them above must not have
        # revoked anything yet.
        self.assertTrue(_live(self.device))
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = _recovery_token.make_token(self.user)
        r = Client().post(f'/accounts/two-factor/recovery-confirm/{uid}/{token}/')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())
        for d in (self.device, self.second):
            self.assertFalse(_live(d))
        self.assertTrue(_live(self.bystander))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class SignOutEverywhereTests(TestCase):

    def setUp(self):
        self.user = make_user('rev-3')
        self.device, self.access, _ = make_device(self.user)
        self.other_phone, _, _ = make_device(self.user, name='Old phone')

    def test_revoke_other_sessions_revokes_every_device(self):
        UserSession.revoke_other_sessions(self.user)
        self.assertFalse(_live(self.device))
        self.assertFalse(_live(self.other_phone))

    def test_runs_even_with_no_other_browser_sessions(self):
        self.assertEqual(UserSession.revoke_other_sessions(self.user, keep_session_key='x'), 0)
        self.assertFalse(_live(self.device))

    def test_the_web_view_doing_it_keeps_its_own_device(self):
        code = Client().post('/api/mobile/v1/auth/web-session/', **bearer(self.access)).json()['code']
        web = Client()
        web.post('/m/handoff/', {'code': code}, **bearer(self.access))
        r = web.post('/account/sessions/revoke-all/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(_live(self.device))
        self.assertFalse(_live(self.other_phone))
        self.assertEqual(web.get('/account/sessions/').status_code, 200)


class LiveChecksTests(TestCase):
    """No hook needed: still_allowed reads these on every request."""

    def setUp(self):
        self.user = make_user('rev-4')
        self.device, self.access, _ = make_device(self.user)

    def _api_status(self):
        return Client().get('/api/mobile/v1/devices/', **bearer(self.access)).status_code

    def test_password_change(self):
        self.assertEqual(self._api_status(), 200)
        self.user.set_password('brand-new-password-9')
        self.user.save()
        self.device.refresh_from_db()
        self.assertEqual(self.device.still_allowed(self.user), (False, 'password_changed'))
        self.assertEqual(self._api_status(), 401)

    def test_quarantine(self):
        type(self.user).objects.filter(pk=self.user.pk).update(is_quarantined=True)
        self.assertFalse(MobileDevice.objects.get(pk=self.device.pk).still_allowed()[0])
        self.assertEqual(self._api_status(), 401)

    def test_deactivation(self):
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(MobileDevice.objects.get(pk=self.device.pk).still_allowed(),
                         (False, 'inactive'))
        self.assertEqual(self._api_status(), 401)

    def test_removed_member(self):
        type(self.user).objects.filter(pk=self.user.pk).update(member_status='Removed')
        self.assertEqual(self._api_status(), 401)
