"""
v3.53.0 — a mobile request checks its access token once.

The site-wide middlewares (``bearer_device``) and DRF's
``MobileTokenAuthentication`` both need the answer; they share
``check_access_token``, memoised on the request per token. Before, each ran
the full check (device + three second-factor lookups), doubling the auth cost
of every mobile request. The memo must not outlive the request: a device
revoked between two requests is refused on the second.
"""
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from src.tests.mobile.sess_helpers import bearer, make_device, make_user


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AccessTokenCheckedOnceTests(TestCase):

    def setUp(self):
        self.user = make_user('once-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def _device_lookups(self, ctx):
        return [q for q in ctx.captured_queries
                if q['sql'].startswith('SELECT') and 'FROM "src_mobiledevice"' in q['sql']
                and '"access_hash"' in q['sql']]

    def test_one_device_lookup_per_request(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self.api.get('/api/mobile/v1/me/', **bearer(self.access))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self._device_lookups(ctx)), 1)

    def test_memo_does_not_outlive_the_request(self):
        self.assertEqual(self.api.get('/api/mobile/v1/me/', **bearer(self.access)).status_code, 200)
        self.device.revoke('test')
        r = self.api.get('/api/mobile/v1/me/', **bearer(self.access))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'signed_out_revoked')

    def test_bad_token_still_refused_with_its_code(self):
        r = self.api.get('/api/mobile/v1/me/', **bearer('not-a-real-token'))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'invalid_token')
