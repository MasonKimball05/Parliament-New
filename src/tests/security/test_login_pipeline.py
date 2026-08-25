"""
Regression tests for the post-auth login pipeline (v3.13.x refactor):

- LoginHistory is written exactly once per login — the single write path
  is signals.log_successful_login (user_logged_in); run_post_auth_pipeline
  no longer writes it (that was the double-write bug).
- A second login from a known IP/location/device is NOT flagged as new —
  the negative case that would have caught the Fernet-ciphertext
  `.filter(ip_address=...)` bug on day one (see the NOTE in
  security_utils.analyze_login_risk).
- The signal handler reuses the pipeline's cached geo instead of making a
  second, uncached ip-api.com call per login (07-04 perf finding).
- Logins that bypass the pipeline (impersonation via login_as_view/admin
  call django login() directly) still get a LoginHistory row, use the
  fallback geo lookup, and skip pipeline extras.

Geo lookups are mocked at both call sites so tests never hit ip-api.com:
- src.geo_utils.get_ip_geo        (pipeline path; is_foreign_ip calls it)
- src.signals.get_geolocation_from_ip  (signal fallback path; patched at
  the signals module because it's imported there at module level)

Run with: python manage.py test src.test_login_pipeline
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from src.models import LoginAlert, LoginHistory, ParliamentUser

PASSWORD = 'testpass123'

# Shape returned by geo_utils.get_ip_geo (lat/lon keys, country_code).
# country_code='US' keeps is_foreign_ip() == False, so no non-US alert path.
FAKE_PIPELINE_GEO = {
    'country': 'United States',
    'country_code': 'US',
    'city': 'Birmingham',
    'region': 'Alabama',
    'lat': 33.52,
    'lon': -86.80,
    'org': '',
    'as': '',
}

# Shape returned by security_utils.get_geolocation_from_ip (latitude/longitude keys).
FAKE_SIGNAL_GEO = {
    'country': 'United States',
    'city': 'Birmingham',
    'region': 'Alabama',
    'latitude': 33.52,
    'longitude': -86.80,
}

TEST_IP = '203.0.113.10'  # TEST-NET-3: public-looking, never routable
TEST_UA = 'Mozilla/5.0 (X11; Linux x86_64) TestClient/1.0'


def make_user(user_id='902', name='Pipeline Tester', username='pipelinetester'):
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


@patch('src.geo_utils.get_ip_geo', return_value=FAKE_PIPELINE_GEO)
@patch('src.signals.get_geolocation_from_ip', return_value=FAKE_SIGNAL_GEO)
class PasswordLoginPipelineTests(TestCase):
    """Password logins go through run_post_auth_pipeline + the signal."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user()

    def _login(self):
        return self.client.post(
            reverse('login'),
            {'username': self.user.username, 'password': PASSWORD},
            REMOTE_ADDR=TEST_IP,
            HTTP_USER_AGENT=TEST_UA,
        )

    def test_exactly_one_login_history_row(self, *_mocks):
        resp = self._login()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            LoginHistory.objects.filter(user=self.user).count(),
            1,
            'LoginHistory must be written exactly once per login '
            '(single write path in signals.log_successful_login)',
        )

    def test_known_ip_not_flagged_as_new(self, *_mocks):
        """Second login, same IP/city/device: no new-anything risk factors.

        Regression guard for the Fernet-ciphertext bug: encrypted
        ip_address can't be matched with .filter(), so a naive filter
        flags EVERY login as a new IP. analyze_login_risk decrypts and
        compares in Python — this test fails if that ever regresses.
        """
        self._login()
        self.client.logout()
        self._login()

        rows = LoginHistory.objects.filter(user=self.user).order_by('timestamp')
        self.assertEqual(rows.count(), 2)
        second = rows.last()

        new_ip_factors = [
            f for f in (second.risk_factors or [])
            if f.startswith('New IP address')
        ]
        self.assertEqual(
            new_ip_factors, [],
            f'Second login from same IP flagged as new IP: {second.risk_factors}',
        )
        # Same city/country and same UA → no new-location / new-device either.
        self.assertEqual(
            [f for f in (second.risk_factors or [])
             if f.startswith(('New login location', 'New device'))],
            [],
            f'Unexpected risk factors on repeat login: {second.risk_factors}',
        )
        self.assertFalse(
            LoginAlert.objects.filter(
                login_history=second,
                alert_type__in=['new_location', 'new_device'],
            ).exists()
        )

    def test_signal_reuses_pipeline_geo(self, mock_signal_geo, _mock_pipeline_geo):
        """Perf guard (07-04 finding): the signal must reuse the pipeline's
        cached geo, not make a second uncached ip-api.com lookup."""
        self._login()
        mock_signal_geo.assert_not_called()
        row = LoginHistory.objects.get(user=self.user)
        # Geo mapped from the pipeline's lat/lon shape onto the row.
        self.assertEqual(row.city, 'Birmingham')
        self.assertEqual(row.latitude, 33.52)


@patch('src.signals.get_geolocation_from_ip', return_value=FAKE_SIGNAL_GEO)
class NonPipelineLoginTests(TestCase):
    """Logins that bypass run_post_auth_pipeline (impersonation via
    login_as_view/admin) call django.contrib.auth.login() directly, so
    request._login_pipeline is absent.

    NOTE: client.force_login() can't be used here — it fires user_logged_in
    with a bare HttpRequest (no REMOTE_ADDR), so LoginHistory's NOT NULL
    ip_address insert fails inside the signal's try/except and poisons the
    test transaction (session save then dies with UpdateError). Calling
    auth.login() with a real RequestFactory request mirrors login_as_view
    exactly (login_as_view.py:30) and exercises the same signal path."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.user = make_user(user_id='903', username='impersonated')

    def _direct_login(self):
        """django.contrib.auth.login() on a real request — the
        impersonation code path, minus the view's authz wrapper."""
        from django.contrib.auth import login
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        request = RequestFactory().get(
            '/', REMOTE_ADDR=TEST_IP, HTTP_USER_AGENT=TEST_UA)
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        login(request, self.user,
              backend='django.contrib.auth.backends.ModelBackend')

    def test_history_written_via_fallback_geo(self, mock_signal_geo):
        self._direct_login()
        self.assertEqual(
            LoginHistory.objects.filter(user=self.user).count(),
            1,
            'Non-pipeline logins must still write exactly one LoginHistory row',
        )
        mock_signal_geo.assert_called_once()  # fallback path taken
        row = LoginHistory.objects.get(user=self.user)
        self.assertEqual(row.city, 'Birmingham')
