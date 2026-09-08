"""
Tests for the custom CSRF_FAILURE_VIEW (v3.26.3).

Context: 08-25-26, members reported CSRF 403s on /login/ and other actions.
Django's default failure view logs only the bare reason string
("CSRF token missing."), which wasn't enough to tell which of several
possible mechanisms (stale bfcache, cross-visitor cache, blocked cookies,
direct-origin bypass) actually produced it. This is the diagnostic
replacement — see src/view/csrf_failure.py's module docstring for what each
logged field is for.
"""
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import ParliamentUser


class CsrfFailureDiagnosticsTests(TestCase):
    def setUp(self):
        # enforce_csrf_checks=True — the default test Client bypasses CSRF
        # entirely, which would make every test here a false pass.
        self.client = Client(enforce_csrf_checks=True)

    def test_no_cookie_at_all_is_rejected_and_logged(self):
        """A first-time visitor / cookies-blocked case — no csrftoken cookie
        was ever set, so Django's reason is 'cookie not set', not 'missing'."""
        with self.assertLogs('security', level='WARNING') as cm:
            resp = self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(cm.output), 1)
        line = cm.output[0]
        self.assertIn('CSRF failure', line)
        self.assertIn('reason=CSRF cookie not set', line)
        self.assertIn('path=/login/', line)
        self.assertIn('method=POST', line)

    def test_the_exact_prod_reported_reason_is_captured(self):
        """
        08-25-26: the reported symptom, verbatim — Django's message is
        specifically "CSRF token missing.", which only happens when the
        cookie IS present but the submitted form has no token field at all
        (as opposed to no cookie ever having been set — see the test above).
        """
        self.client.get(reverse('login'))  # establishes a real csrftoken cookie
        with self.assertLogs('security', level='WARNING') as cm:
            resp = self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 403)
        line = cm.output[0]
        self.assertIn('reason=CSRF token missing', line)
        self.assertIn('has_csrf_cookie=True', line)
        self.assertIn('posted_token_present=False', line)

    def test_it_renders_the_apps_own_403_page_not_djangos_default(self):
        resp = self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 403)
        # This app's 403.html, not django/views/templates/csrf_403.html —
        # the generic Django template does not carry this app's chrome.
        self.assertIn(b'</html>', resp.content)
        self.assertNotIn(b'CSRF verification failed. Request aborted.', resp.content)

    def test_the_403_response_is_not_cacheable(self):
        resp = self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        self.assertIn('no-store', resp.get('Cache-Control', ''))

    def test_logs_whether_the_csrf_cookie_itself_was_present(self):
        # No cookies at all — has_csrf_cookie AND has_session_cookie should
        # both read False, which is the "cookies are being dropped/blocked
        # wholesale" signature rather than a CSRF-specific one.
        with self.assertLogs('security', level='WARNING') as cm:
            self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        line = cm.output[0]
        self.assertIn('has_csrf_cookie=False', line)
        self.assertIn('has_session_cookie=False', line)

    def test_logs_true_when_the_csrf_cookie_is_present_but_the_token_is_wrong(self):
        # GET first so the client actually holds a csrftoken cookie, then POST
        # a same-length-but-wrong token value — has_csrf_cookie AND
        # posted_token_present should both read True, distinguishing this
        # from the "no token submitted at all" case above.
        self.client.get(reverse('login'))
        real_token = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertIn(settings.CSRF_COOKIE_NAME, self.client.cookies)

        wrong_token = ('x' * len(real_token))
        with self.assertLogs('security', level='WARNING') as cm:
            resp = self.client.post(reverse('login'), {
                'username': 'x', 'password': 'y',
                'csrfmiddlewaretoken': wrong_token,
            })
        self.assertEqual(resp.status_code, 403)
        line = cm.output[0]
        self.assertIn('has_csrf_cookie=True', line)
        self.assertIn('posted_token_present=True', line)
        self.assertIn('reason=CSRF token from POST incorrect', line)

    def test_never_logs_the_actual_cookie_or_token_value(self):
        """
        The whole point of this view is a diagnostic that is safe to keep
        around — it must not itself become a place secrets end up.
        """
        self.client.get(reverse('login'))
        real_cookie_value = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertTrue(real_cookie_value)

        with self.assertLogs('security', level='WARNING') as cm:
            self.client.post(reverse('login'), {
                'username': 'x', 'password': 'y',
                'csrfmiddlewaretoken': 'a-value-that-must-never-appear-in-logs',
            })
        line = cm.output[0]
        self.assertNotIn(real_cookie_value, line)
        self.assertNotIn('a-value-that-must-never-appear-in-logs', line)

    def test_referer_origin_cf_ray_and_user_agent_are_captured(self):
        with self.assertLogs('security', level='WARNING') as cm:
            self.client.post(
                reverse('login'), {'username': 'x', 'password': 'y'},
                HTTP_REFERER='https://am-parliament.org/login/',
                HTTP_ORIGIN='https://am-parliament.org',
                HTTP_CF_RAY='abc123-DFW',
                HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
                HTTP_SEC_FETCH_SITE='same-origin',
            )
        line = cm.output[0]
        self.assertIn('referer=https://am-parliament.org/login/', line)
        self.assertIn('origin=https://am-parliament.org', line)
        self.assertIn('cf_ray=abc123-DFW', line)
        self.assertIn('sec_fetch_site=same-origin', line)
        self.assertIn('iPhone', line)

    def test_absent_headers_render_as_a_dash_not_a_blank(self):
        """
        A blank field in a pipe-delimited log line is ambiguous (empty
        string vs field not sent); a literal '-' is not.
        """
        with self.assertLogs('security', level='WARNING') as cm:
            self.client.post(reverse('login'), {'username': 'x', 'password': 'y'})
        line = cm.output[0]
        self.assertIn('referer=-', line)
        self.assertIn('origin=-', line)
        self.assertIn('cf_ray=-', line)

    def test_authenticated_user_is_identified_in_the_log(self):
        user = ParliamentUser.objects.create_user(
            user_id='csrf-test-1', password='csrf-diag-test-pass-1!',
            name='CSRF Tester', username='csrf_tester', member_type='Member',
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        with self.assertLogs('security', level='WARNING') as cm:
            client.post('/preferences/', {'theme': 'dark'})
        line = cm.output[0]
        self.assertIn('csrf-test-1', line)
        self.assertIn('csrf_tester', line)
        self.assertNotIn('user=anonymous', line)

    def test_settings_points_at_the_diagnostic_view(self):
        self.assertEqual(settings.CSRF_FAILURE_VIEW, 'src.view.csrf_failure.csrf_failure')
