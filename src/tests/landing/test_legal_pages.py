"""
v4.4.0: the privacy policy, terms and support pages are public. The App Store
listing links to them, so they must load signed out (App Review checks).
"""
from django.test import TestCase
from django.urls import reverse


class LegalPagesTests(TestCase):
    def test_public_pages_load_signed_out(self):
        for name, text in [('privacy_policy', 'Deleting your account'),
                           ('terms', 'no tolerance for objectionable content'),
                           ('support', 'Delete account')]:
            with self.subTest(name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, text)

    def test_support_form_posts_to_the_contact_inbox(self):
        r = self.client.get(reverse('support'))
        self.assertContains(r, reverse('contact_submit'))

    def test_sign_in_and_landing_link_to_them(self):
        for page in (reverse('login'), reverse('landing')):
            r = self.client.get(page)
            for name in ('privacy_policy', 'terms', 'support'):
                self.assertContains(r, f'href="{reverse(name)}"', msg_prefix=page)
