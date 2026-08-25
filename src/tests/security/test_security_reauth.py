"""
Regression tests for v3.5.1 / v3.5.2 security hardening:

- Disabling 2FA requires password re-authentication (+ rate limit)
- /accounts/two-factor/disable/ is NOT reachable by a session that hasn't
  completed 2FA verification (removed from middleware exempt list)
- Voluntary 2FA (policy doesn't require it) is still enforced at login
- Passkey deletion requires password re-authentication (+ rate limit)
- Passkey registration (begin) requires password re-authentication
- Security notification emails fire on 2FA disable / passkey add / delete

Run with: python manage.py test src.test_security_reauth
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.core import mail
from django.core.cache import cache
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser, WebAuthnCredential
from src.models_feature_flags import SiteSetting

PASSWORD = 'testpass123'


def make_user(user_id='901', name='Reauth Tester', username='reauthtester'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id,
        name=name,
        username=username,
        member_type='Member',
    )
    user.username = username  # create_user overwrites username with name
    user.set_password(PASSWORD)
    user.email = f'{username}@parliament.test'
    user.save()
    return user


class TwoFactorDisableReauthTests(TestCase):
    """two_factor_disable must demand the current password."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user()
        self.device = TOTPDevice.objects.create(
            user=self.user, name='default', confirmed=True
        )
        self.client.force_login(self.user)
        # Mark the session OTP-verified so the middleware lets us reach the page
        self._verify_session()

    def _verify_session(self):
        session = self.client.session
        session['otp_device_id'] = self.device.persistent_id
        session.save()

    def test_disable_without_password_keeps_devices(self):
        self.client.post(reverse('two_factor_disable'), {})
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists(),
            '2FA devices must survive a POST with no password',
        )

    def test_disable_with_wrong_password_keeps_devices(self):
        self.client.post(reverse('two_factor_disable'), {'password': 'wrong-password'})
        self.assertTrue(TOTPDevice.objects.filter(user=self.user, confirmed=True).exists())

    def test_disable_with_correct_password_removes_devices(self):
        response = self.client.post(
            reverse('two_factor_disable'), {'password': PASSWORD}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())

    def test_disable_sends_notification_email(self):
        self.client.post(reverse('two_factor_disable'), {'password': PASSWORD})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Two-Factor Authentication was disabled', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, [self.user.email])

    def test_disable_rate_limit_after_five_failures(self):
        for _ in range(5):
            self.client.post(reverse('two_factor_disable'), {'password': 'nope'})
        # Sixth attempt with the CORRECT password must still be refused
        self.client.post(reverse('two_factor_disable'), {'password': PASSWORD})
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists(),
            'Rate limit must block even a correct password after 5 failures',
        )


class TwoFactorMiddlewareGateTests(TestCase):
    """The disable endpoint must be unreachable pre-verification, and
    voluntary 2FA must be enforced at login."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user(user_id='902', username='gatetester')
        self.device = TOTPDevice.objects.create(
            user=self.user, name='default', confirmed=True
        )
        # Global policy does NOT require 2FA — the user enabled it voluntarily
        SiteSetting.set_setting('2fa_policy_mode', 'none')

    def test_unverified_session_cannot_reach_disable(self):
        """Password-authenticated but not OTP-verified → redirected, not served."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('two_factor_disable'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('two_factor_verify'), response.url)

    def test_unverified_post_cannot_disable(self):
        """The POST itself must also be intercepted by the middleware."""
        self.client.force_login(self.user)
        self.client.post(reverse('two_factor_disable'), {'password': PASSWORD})
        self.assertTrue(
            TOTPDevice.objects.filter(user=self.user, confirmed=True).exists(),
            'An unverified session must not be able to disable 2FA even with the password',
        )

    def test_voluntary_2fa_is_enforced(self):
        """Policy 'none' + confirmed device → ordinary pages still redirect to verify."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('two_factor_verify'), response.url)

    def test_user_without_device_not_gated(self):
        """Policy 'none' + no device → no verify redirect (sanity check)."""
        plain = make_user(user_id='903', username='nodevice')
        self.client.force_login(plain)
        response = self.client.get(reverse('profile'))
        self.assertNotEqual(response.status_code, 302)


class PasskeyDeleteReauthTests(TestCase):
    """passkey_delete must demand the current password."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user(user_id='904', username='pktester')
        self.cred = WebAuthnCredential.objects.create(
            user=self.user,
            credential_id=b'test-credential-id-904',
            public_key=b'test-public-key',
            sign_count=0,
            name='Test Passkey',
        )
        self.client.force_login(self.user)

    def _delete_url(self):
        return reverse('passkey_delete', args=[self.cred.pk])

    def test_delete_without_password_keeps_passkey(self):
        self.client.post(self._delete_url(), {})
        self.assertTrue(WebAuthnCredential.objects.filter(pk=self.cred.pk).exists())

    def test_delete_with_wrong_password_keeps_passkey(self):
        self.client.post(self._delete_url(), {'password': 'wrong'})
        self.assertTrue(WebAuthnCredential.objects.filter(pk=self.cred.pk).exists())

    def test_delete_with_correct_password_removes_passkey(self):
        self.client.post(self._delete_url(), {'password': PASSWORD})
        self.assertFalse(WebAuthnCredential.objects.filter(pk=self.cred.pk).exists())

    def test_delete_sends_notification_email(self):
        self.client.post(self._delete_url(), {'password': PASSWORD})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('passkey was removed', mail.outbox[0].subject)

    def test_delete_rate_limit_after_five_failures(self):
        for _ in range(5):
            self.client.post(self._delete_url(), {'password': 'nope'})
        self.client.post(self._delete_url(), {'password': PASSWORD})
        self.assertTrue(
            WebAuthnCredential.objects.filter(pk=self.cred.pk).exists(),
            'Rate limit must block even a correct password after 5 failures',
        )

    def test_cannot_delete_another_users_passkey(self):
        other = make_user(user_id='905', username='otheruser')
        other_cred = WebAuthnCredential.objects.create(
            user=other,
            credential_id=b'test-credential-id-905',
            public_key=b'pk',
            sign_count=0,
        )
        response = self.client.post(
            reverse('passkey_delete', args=[other_cred.pk]), {'password': PASSWORD}
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebAuthnCredential.objects.filter(pk=other_cred.pk).exists())


class PasskeyRegisterReauthTests(TestCase):
    """passkey_register_begin must demand the current password."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user(user_id='906', username='regtester')
        self.client.force_login(self.user)
        self.url = reverse('passkey_register_begin')

    def test_begin_without_password_is_refused(self):
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('challenge', response.json())

    def test_begin_with_wrong_password_is_refused(self):
        response = self.client.post(self.url, {'password': 'wrong'})
        self.assertEqual(response.status_code, 403)

    def test_begin_with_correct_password_returns_options(self):
        response = self.client.post(self.url, {'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn('challenge', response.json())

    def test_begin_rate_limit_after_five_failures(self):
        for _ in range(5):
            self.client.post(self.url, {'password': 'nope'})
        response = self.client.post(self.url, {'password': PASSWORD})
        self.assertEqual(
            response.status_code, 429,
            'Rate limit must block even a correct password after 5 failures',
        )

    def test_begin_requires_login(self):
        self.client.logout()
        response = self.client.post(self.url, {'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
