"""
v3.28.4. `src/view/csrf_token.py` — the server side of the silent
bfcache/mobile CSRF fix in `src/tests/security/test_bfcache_reload.py`. Own
file rather than folded into that one: this exercises a real view through
the test client, while that file is purely structural (parses base.html).
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser


class CsrfTokenRefreshViewTests(TestCase):
    PASSWORD = 'csrf-refresh-test-pass-12345!'

    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='MEL-CSRFREFRESH', password=self.PASSWORD, name='CSRF Refresh Tester',
            username='mel_csrfrefresh', member_type='Member', is_admin=False,
        )

    def test_authenticated_get_returns_a_token(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('csrf_token_refresh'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('csrfToken', data)
        self.assertTrue(data['csrfToken'])

    def test_anonymous_get_also_returns_a_token(self):
        """
        ⚠️ DELIBERATE. CSRF protection applies to anonymous sessions too (a
        public contact form, for instance), and this endpoint discloses
        nothing a normal page render doesn't already put in
        `{% csrf_token %}` / the `<meta name="csrf-token">` tag — a
        `@login_required` here would just reintroduce the bug for the one
        population that can't log in to get past it.
        """
        response = self.client.get(reverse('csrf_token_refresh'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrfToken', response.json())

    def test_post_is_not_allowed(self):
        response = self.client.post(reverse('csrf_token_refresh'))
        self.assertEqual(response.status_code, 405)

    def test_response_is_never_cached(self):
        response = self.client.get(reverse('csrf_token_refresh'))
        self.assertEqual(response.headers.get('Cache-Control'), 'no-store')

    def test_the_returned_token_is_accepted_by_a_real_post(self):
        """
        The point of the whole feature: a token minted by THIS endpoint,
        patched into a form field by the client-side JS, must actually pass
        CSRF validation on a real POST — not just look like a token.

        Django's default test `Client` doesn't enforce CSRF at all (that's
        why every other test in this file can POST freely), so this uses
        `enforce_csrf_checks=True` deliberately — the one test in this file
        where that matters. `/login/` is the endpoint the mobile-CSRF saga
        was originally reported on, so it's also the most on-theme choice:
        wrong credentials with a VALID token must fail for "bad password,"
        not "bad token."
        """
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get(reverse('login'))  # establishes the csrftoken cookie this client will carry
        # The token itself comes from THIS endpoint, not from the page render
        # above — that's the thing under test.
        token = csrf_client.get(reverse('csrf_token_refresh')).json()['csrfToken']

        response = csrf_client.post(
            reverse('login'),
            {'username': 'does-not-exist', 'password': 'wrong', 'csrfmiddlewaretoken': token},
        )

        self.assertNotEqual(
            response.status_code, 403,
            'A valid CSRF token was rejected — the login attempt should '
            'fail for "bad credentials," not "bad token."',
        )

    def test_a_missing_token_is_rejected_by_the_same_client(self):
        """
        Control for the test above — proves `enforce_csrf_checks=True`
        actually enforces something, so the previous test's "not 403" is
        meaningful rather than a client that never checks in the first
        place.
        """
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get(reverse('login'))  # establish the csrftoken cookie

        response = csrf_client.post(
            reverse('login'),
            {'username': 'does-not-exist', 'password': 'wrong'},  # no csrfmiddlewaretoken
        )

        self.assertEqual(response.status_code, 403)
