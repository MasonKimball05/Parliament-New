"""
v3.15.2 — get_geolocation_from_ip must not crash on a missing IP.

Regression for the recurring '[ERROR] security: Error tracking login ...
NoneType ... startswith' — get_client_ip returns None when REMOTE_ADDR is
absent (sessionless/test requests, some proxy setups), and the geo lookup
called .startswith on it, killing login-history tracking for that login.
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from src.utils.security_utils import get_geolocation_from_ip


class GeoIpGuardTests(SimpleTestCase):
    def test_none_ip_returns_unknown_not_crash(self):
        result = get_geolocation_from_ip(None)
        self.assertEqual(result['country'], 'Unknown')
        self.assertIsNone(result['latitude'])

    def test_empty_ip_returns_unknown(self):
        self.assertEqual(get_geolocation_from_ip('')['country'], 'Unknown')

    def test_unknown_sentinel_returns_unknown_without_http(self):
        # signals.py substitutes the literal 'unknown' for a missing IP. It
        # must short-circuit to the Unknown dict, NOT fall through to a live
        # ip-api.com lookup for /json/unknown (wasted 3s-timeout request on the
        # failed-login path, which has no cached pipeline geo).
        with patch('src.utils.security_utils.requests.get') as mock_get:
            result = get_geolocation_from_ip('unknown')
        self.assertEqual(result['country'], 'Unknown')
        self.assertIsNone(result['latitude'])
        mock_get.assert_not_called()

    def test_private_ip_still_local(self):
        self.assertEqual(
            get_geolocation_from_ip('192.168.1.10')['country'], 'Local Network')
        self.assertEqual(
            get_geolocation_from_ip('127.0.0.1')['country'], 'Local Network')
