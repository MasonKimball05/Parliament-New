"""
v3.15.2 — geo lookup circuit breaker + delegation.

The 07-19 502 incident: /login/ (incl. failed logins) did an UNCACHED,
blocking external geo call; under a brute-force flood the stalled calls
wedged Daphne. These tests pin the two fixes: the breaker short-circuits
after repeated failures, and get_geolocation_from_ip now routes through the
cached+breakered get_ip_geo instead of its own requests.get.
"""
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from src import geo_utils
from src.utils.security_utils import get_geolocation_from_ip


class GeoBreakerTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _fail_response(self):
        m = mock.Mock()
        m.json.return_value = {'status': 'fail', 'message': 'quota'}
        return m

    def test_breaker_opens_after_threshold_and_short_circuits(self):
        call_count = {'n': 0}

        def fake_get(*a, **k):
            call_count['n'] += 1
            return self._fail_response()

        with mock.patch('src.geo_utils.requests.get', side_effect=fake_get):
            # Distinct IPs so the per-IP cache never satisfies the lookup.
            for i in range(20):
                geo_utils.get_ip_geo(f'8.8.{i}.{i}')
        # Breaker trips at 5 failures, then all further lookups skip the call.
        self.assertLessEqual(call_count['n'], geo_utils._BREAKER_FAIL_THRESHOLD)
        self.assertTrue(geo_utils._breaker_is_open())

    def test_success_resets_breaker(self):
        ok = mock.Mock()
        ok.json.return_value = {'status': 'success', 'country': 'United States',
                                'countryCode': 'US', 'city': 'Birmingham',
                                'regionName': 'AL', 'lat': 33.5, 'lon': -86.8}
        # Prime a couple of failures, then a success clears the count.
        with mock.patch('src.geo_utils.requests.get',
                        side_effect=[self._fail_response(), ok]):
            geo_utils.get_ip_geo('8.8.4.4')
            geo_utils.get_ip_geo('8.8.4.5')
        self.assertIsNone(cache.get(geo_utils._BREAKER_FAIL_KEY))
        self.assertFalse(geo_utils._breaker_is_open())

    def test_open_breaker_still_serves_cached_ip(self):
        cache.set('geo_1.2.3.4', {'country': 'X', 'city': 'Y', 'region': 'Z',
                                  'lat': 1, 'lon': 2}, 60)
        cache.set(geo_utils._BREAKER_OPEN_KEY, True, 60)
        with mock.patch('src.geo_utils.requests.get') as g:
            out = geo_utils.get_ip_geo('1.2.3.4')
            g.assert_not_called()  # served from cache, no external call
        self.assertEqual(out['country'], 'X')

    def test_get_geolocation_delegates_and_maps_shape(self):
        with mock.patch('src.utils.security_utils.get_ip_geo',
                        return_value={'country': 'United States', 'city': 'Birmingham',
                                      'region': 'AL', 'lat': 33.5, 'lon': -86.8}):
            out = get_geolocation_from_ip('9.9.9.9')
        self.assertEqual(out['country'], 'United States')
        self.assertEqual(out['latitude'], 33.5)   # lat -> latitude mapping
        self.assertEqual(out['longitude'], -86.8)

    def test_get_geolocation_fast_paths_make_no_call(self):
        with mock.patch('src.utils.security_utils.get_ip_geo') as g:
            self.assertEqual(get_geolocation_from_ip(None)['country'], 'Unknown')
            self.assertEqual(
                get_geolocation_from_ip('192.168.1.1')['country'], 'Local Network')
            g.assert_not_called()
