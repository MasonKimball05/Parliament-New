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


class CsrfTokenRefreshDiagnosticLoggingTests(TestCase):
    """
    v3.29.26 — TEMPORARY. Added to answer one question live, without
    needing Safari's remote inspector: does the client-side
    `refreshCsrfToken()` (base.html) ever actually reach this endpoint on
    the still-failing mobile page, or does the JS never get that far? This
    endpoint is the only place that function talks to the server, so a
    hit here is proof the safety net ran — logged to the `security`
    logger (same file `csrf_failure.py` already writes the 403 itself
    into), not `ActivityLog` (this is debugging noise, not an audit-trail
    event). Remove this whole class along with the logging it tests once
    the root cause is confirmed and fixed — see the module docstring.
    """
    PASSWORD = 'csrf-refresh-diag-test-pass-12345!'

    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='MEL-CSRFDIAG', password=self.PASSWORD, name='CSRF Diag Tester',
            username='mel_csrfdiag', member_type='Member', is_admin=False,
        )

    def test_a_hit_is_logged(self):
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))
        self.assertTrue(
            any('CSRF token refresh called' in record for record in logs.output),
            'the diagnostic log line did not fire on a real hit to this endpoint',
        )

    def test_it_does_not_log_the_token_value(self):
        """
        Same reasoning as `csrf_failure.py`'s own comment: a security log
        is itself an asset — logging the secret it's trying to protect
        defeats the point.
        """
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            response = self.client.get(reverse('csrf_token_refresh'))
        token = response.json()['csrfToken']
        combined = '\n'.join(logs.output)
        self.assertNotIn(token, combined)

    def test_it_identifies_an_authenticated_user(self):
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))
        combined = '\n'.join(logs.output)
        self.assertIn('MEL-CSRFDIAG', combined)
        self.assertIn('mel_csrfdiag', combined)

    def test_it_labels_an_anonymous_hit(self):
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))
        combined = '\n'.join(logs.output)
        self.assertIn('anonymous', combined)

    def test_it_records_whether_a_token_came_back(self):
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))
        combined = '\n'.join(logs.output)
        self.assertIn('returned_a_token=True', combined)

    def test_it_records_whether_the_csrf_cookie_already_existed(self):
        """
        The key diagnostic field for the live question this exists to
        answer: a mobile session with `has_csrf_cookie=True` on the FAILED
        request (per the production log) but `had_csrf_cookie_before=False`
        here would mean this endpoint minted a brand new cookie — i.e. the
        one the failing POST carried wasn't the one this refresh call, if
        any, actually produced.
        """
        self.client.force_login(self.user)
        # First hit establishes the cookie on this client.
        self.client.get(reverse('csrf_token_refresh'))
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))
        combined = '\n'.join(logs.output)
        self.assertIn('had_csrf_cookie_before=True', combined)


class CsrfTokenRefreshSourceTaggingTests(TestCase):
    """
    v3.29.28 — TEMPORARY. `refreshCsrfToken()` in base.html has two
    independent callers (the `pageshow` handler and the submit-safety-net
    listener) that both hit this endpoint and, before this change,
    produced an identical-looking log line either way. `?source=` closes
    that gap — see the module docstring's v3.29.28 entry. Remove
    alongside the rest of this temporary logging once the root cause is
    confirmed and fixed.
    """
    PASSWORD = 'csrf-refresh-source-test-pass-12345!'

    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='MEL-CSRFSRC', password=self.PASSWORD, name='CSRF Source Tester',
            username='mel_csrfsrc', member_type='Member', is_admin=False,
        )

    def test_a_pageshow_sourced_hit_is_labeled(self):
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'), {'source': 'pageshow'})
        combined = '\n'.join(logs.output)
        self.assertIn('source=pageshow', combined)

    def test_a_submit_sourced_hit_is_labeled(self):
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'), {'source': 'submit'})
        combined = '\n'.join(logs.output)
        self.assertIn('source=submit', combined)

    def test_a_hit_with_no_source_reads_as_a_dash_not_blank_or_missing(self):
        """
        Control — same "ambiguous blank" reasoning csrf_failure.py already
        applies to its own absent-header fields.
        """
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            self.client.get(reverse('csrf_token_refresh'))  # no ?source= at all
        combined = '\n'.join(logs.output)
        self.assertIn('source=-', combined)

    def test_an_unrecognized_source_value_is_logged_verbatim_not_rejected(self):
        """
        This is a diagnostic label, not a validated enum — an unexpected
        value (e.g. a stale deployed JS build sending something else)
        should still show up rather than 400 or silently become '-'.
        """
        self.client.force_login(self.user)
        with self.assertLogs('security', level='INFO') as logs:
            response = self.client.get(reverse('csrf_token_refresh'), {'source': 'something-unexpected'})
        self.assertEqual(response.status_code, 200)
        combined = '\n'.join(logs.output)
        self.assertIn('source=something-unexpected', combined)
