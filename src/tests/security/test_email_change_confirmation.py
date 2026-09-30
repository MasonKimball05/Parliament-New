"""
v3.37.0 (09-30-26) — "the email confirmation to change your Parliament email
isn't working".

The confirmation flow itself works when the link is opened in a signed-in,
already-verified browser (pinned below, since it had no tests at all). What
broke is opening it SIGNED OUT, which is the usual case: the email is read on a
phone, where the site isn't signed in. The link goes to the login page with
?next=, and:

  * members with 2FA: login → Enforce2FAMiddleware redirected to the verify
    page WITHOUT next → verify sent them home. The email never changed, and
    nothing said so.
  * passkey sign-in: always returned {'redirect': '/'}, ignoring next.

Only a password login by a member without 2FA survived the trip.

Run with: python manage.py test src.tests.security.test_email_change_confirmation
"""
import re

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser
from src.next_url import safe_next, with_next
from src.tests.security.test_two_factor import generate_totp

PASSWORD = 'Pw-Email-Change-12345!'


@override_settings(REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_HOST_USER='x@example.com', BEHIND_CLOUDFLARE=False)
class EmailChangeConfirmationTests(TestCase):
    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='emc1', name='Casey', username='emc1', member_type='Member', password=PASSWORD)
        self.user.email = 'old@example.com'
        self.user.onboarding_complete = True
        self.user.save()

    def _request_change(self):
        self.client.force_login(self.user)
        self.client.post(reverse('set_email'), {'email': 'new@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        url = re.search(r'https?://\S+/set-email/confirm/[0-9a-f-]+/', mail.outbox[0].body).group(0)
        return re.sub(r'^https?://[^/]+', '', url)

    def assertChanged(self):
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.com')

    def test_signed_in_click(self):
        path = self._request_change()
        self.client.get(path)
        self.assertChanged()

    def test_signed_out_click_password_login(self):
        path = self._request_change()
        self.client.logout()
        r = self.client.get(path)
        r = self.client.post(r['Location'], {'username': 'emc1', 'password': PASSWORD})
        self.assertEqual(r['Location'], path)
        self.client.get(path)
        self.assertChanged()

    def test_signed_out_click_with_2fa(self):
        path = self._request_change()
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.client.logout()
        r = self.client.get(path)
        r = self.client.post(r['Location'], {'username': 'emc1', 'password': PASSWORD})
        r = self.client.get(r['Location'])                    # → 2FA verify
        verify = r['Location']
        self.assertTrue(verify.startswith(reverse('two_factor_verify') + '?next='), verify)
        r = self.client.post(verify, {'token': generate_totp(device)})
        self.assertEqual(r['Location'], path)                 # was: /home/
        self.client.get(path)
        self.assertChanged()

    def test_verify_ignores_an_offsite_next(self):
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.client.force_login(self.user)
        r = self.client.post(reverse('two_factor_verify') + '?next=https://evil.example/x',
                             {'token': generate_totp(device)})
        self.assertEqual(r['Location'], reverse('home'))

    def test_passkey_pages_forward_their_query_string(self):
        for tpl in ('templates/registration/login.html', 'templates/two_factor/verify.html'):
            with open(tpl) as f:
                self.assertIn("""{% url "passkey_authenticate_complete" %}' + window.location.search""", f.read(), tpl)


class NextUrlHelperTests(TestCase):
    # Paths are built from reverse() so the hard-coded-URL guard stays happy.
    def setUp(self):
        self.rf = RequestFactory()
        self.home = reverse('home')
        self.verify = reverse('two_factor_verify')

    def test_safe_next(self):
        self.assertEqual(safe_next(self.rf.get(self.home, {'next': self.home})), self.home)
        self.assertEqual(safe_next(self.rf.get(self.home, {'next': 'https://evil.example'})), '')
        self.assertEqual(safe_next(self.rf.get(self.home, {'next': '//evil.example'})), '')
        self.assertEqual(safe_next(self.rf.get(self.home)), '')

    def test_with_next_only_for_page_loads(self):
        self.assertEqual(with_next(self.verify, self.rf.get(self.home, {'c': '1'})),
                         f'{self.verify}?next={self.home}%3Fc%3D1')
        self.assertEqual(with_next(self.verify, self.rf.post(self.home)), self.verify)
        self.assertEqual(with_next(self.verify, self.rf.get(self.home, HTTP_X_REQUESTED_WITH='XMLHttpRequest')),
                         self.verify)
