"""
v4.4.0 — a taken username on the Profile page.

The username went straight to ``user.save()``, so picking one another member
already has raised an IntegrityError: a 500. ``save_account_fields`` (shared
with the app since v4.4.0) now refuses it like any other bad edit.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser


def make_user(uid, **kwargs):
    defaults = dict(name=f'{uid} User', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('taken-username-test-12345!')
    user.save()
    return user


class TakenUsernameTests(TestCase):
    def setUp(self):
        self.user = make_user('UN-U1')
        make_user('UN-U2')
        self.client.login(username=self.user.username, password='taken-username-test-12345!')

    def post(self, **headers):
        return self.client.post(reverse('profile'), {
            'profile_submit': '1', 'username': 'UN-U2', 'preferred_name': '',
            'email': '', 'phone_number': '',
        }, **headers)

    def test_page_shows_an_error_instead_of_crashing(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'UN-U1')

    def test_ajax_save_is_refused(self):
        response = self.post(HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertIn('taken', response.content.decode())
