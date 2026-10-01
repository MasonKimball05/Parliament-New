"""
v3.38.2 (10-01-26) — the 2FA self-service recovery link works once.

Before: the token hash was pk + password + last_login, and the confirm view
only called login() when the browser was NOT already signed in as the member.
In the normal flow it is (the request page is @login_required), so nothing
the token depended on changed and the link stayed valid until it expired. A
replay from any other browser wiped the member's new authenticator and signed
that browser in as them. The wipe also ran on GET, so a mail scanner fetching
the link performed it.

Pins (src/view/two_factor_recovery.py):
  * GET changes nothing; the wipe is POST only;
  * after one use the link is dead, from the same browser and from another;
  * a link issued before a re-enrolment does not survive it.

Run with: python manage.py test src.tests.security.test_two_factor_recovery
"""
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser
from src.view.two_factor_recovery import _recovery_token


class RecoveryLinkTests(TestCase):
    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='rec-1', name='Recovery Member', username='rec-1',
            email='rec-1@example.com', member_type='Member')
        TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        StaticDevice.objects.create(user=self.user, name='backup')
        # The member's own browser: signed in with the password, 2FA pending.
        self.client.force_login(self.user)
        self.user.refresh_from_db()
        self.url = reverse('two_factor_recovery_confirm', args=[
            urlsafe_base64_encode(force_bytes(self.user.pk)),
            _recovery_token.make_token(self.user)])

    def devices(self):
        return (TOTPDevice.objects.filter(user=self.user).count()
                + StaticDevice.objects.filter(user=self.user).count())

    def test_get_changes_nothing(self):
        response = Client().get(self.url)     # a mail scanner: no session
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'two_factor/recovery_confirm.html')
        self.assertEqual(self.devices(), 2)
        self.assertNotIn('_auth_user_id', response.client.session)

    def test_post_wipes_and_goes_to_setup(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('two_factor_setup'), fetch_redirect_response=False)
        self.assertEqual(self.devices(), 0)

    def test_link_is_dead_after_use_in_the_members_own_browser(self):
        self.client.post(self.url)
        # The member re-enrols; someone else then replays the same link.
        TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        for method in ('get', 'post'):
            other = Client()
            response = getattr(other, method)(self.url)
            self.assertEqual(response.status_code, 400, method)
            self.assertNotIn('_auth_user_id', other.session, method)
        self.assertEqual(self.devices(), 1)

    def test_link_is_dead_after_use_before_re_enrolment(self):
        self.client.post(self.url)
        other = Client()
        self.assertEqual(other.post(self.url).status_code, 400)
        self.assertNotIn('_auth_user_id', other.session)

    def test_link_issued_before_a_device_change_is_dead(self):
        TOTPDevice.objects.filter(user=self.user).delete()
        TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.assertEqual(Client().post(self.url).status_code, 400)
        self.assertEqual(self.devices(), 2)

    def test_bad_token_is_rejected(self):
        bad = reverse('two_factor_recovery_confirm', args=[
            urlsafe_base64_encode(force_bytes(self.user.pk)), 'abc-def'])
        self.assertEqual(self.client.post(bad).status_code, 400)
        self.assertEqual(self.devices(), 2)
