"""
IP geolocation is a LOCAL GeoLite2 lookup — member IPs never leave the server.

Replaces the v3.15.2 circuit-breaker tests (test_geo_breaker.py): the breaker
existed to bound blocking calls to ip-api.com, and there is no external call
any more. These pin the new contract:

- no outbound HTTP, ever, from any lookup path;
- a missing database gives the old "lookup failed" result ({}), warns once,
  and logins still work;
- a known IP resolves through the reader into the same dict shape as before
  (isp/org/as present but empty — GeoLite2 City has no ISP data).

A fake reader stands in for the .mmdb file, so these never depend on whether
the machine running them has a GeoLite2 database installed.
"""
import os
import tempfile
from unittest import mock

from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from src import geo_utils
from src.models import LoginHistory, ParliamentUser
from src.utils.security_utils import get_geolocation_from_ip

MISSING_DB = os.path.join(tempfile.gettempdir(), 'no-such-geoip-dir', 'GeoLite2-City.mmdb')

# A GeoLite2-City record as maxminddb returns it.
PARIS_RECORD = {
    'city': {'geoname_id': 2988507, 'names': {'en': 'Paris', 'fr': 'Paris'}},
    'continent': {'code': 'EU', 'names': {'en': 'Europe'}},
    'country': {'iso_code': 'FR', 'names': {'en': 'France'}},
    'location': {'latitude': 48.8534, 'longitude': 2.3488, 'accuracy_radius': 20},
    'postal': {'code': '75001'},
    'registered_country': {'iso_code': 'FR', 'names': {'en': 'France'}},
    'subdivisions': [{'iso_code': 'IDF', 'names': {'en': 'Île-de-France'}}],
}
BIRMINGHAM_RECORD = {
    'city': {'names': {'en': 'Birmingham'}},
    'country': {'iso_code': 'US', 'names': {'en': 'United States'}},
    'location': {'latitude': 33.52, 'longitude': -86.80},
    'postal': {'code': '35203'},
    'subdivisions': [{'iso_code': 'AL', 'names': {'en': 'Alabama'}}],
}


class FakeReader:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get(self, ip):
        self.calls.append(ip)
        if ip == 'not-an-ip':
            raise ValueError('not a valid IP address')
        return self.records.get(ip)

    def close(self):
        pass


class _NoNetworkMixin:
    """Fails the test on any outbound connection attempt."""

    def setUp(self):
        super().setUp()
        geo_utils._reset()
        self.addCleanup(geo_utils._reset)
        cache.clear()
        boom = AssertionError('outbound network call during a geo lookup')
        for target in ('requests.get', 'requests.post', 'requests.Session.request',
                       'urllib.request.urlopen', 'socket.create_connection'):
            p = mock.patch(target, side_effect=boom)
            setattr(self, 'net_' + target.replace('.', '_'), p.start())
            self.addCleanup(p.stop)

    def assert_no_network(self):
        for name in dir(self):
            if name.startswith('net_'):
                getattr(self, name).assert_not_called()


class LocalLookupTests(_NoNetworkMixin, SimpleTestCase):

    def _with_reader(self, records):
        reader = FakeReader(records)
        p = mock.patch.object(geo_utils, '_get_reader', return_value=reader)
        p.start()
        self.addCleanup(p.stop)
        return reader

    def test_known_ip_resolves_to_the_old_shape(self):
        self._with_reader({'198.51.100.7': PARIS_RECORD})
        geo = geo_utils.get_ip_geo('198.51.100.7')
        self.assertEqual(geo, {
            'country': 'France', 'country_code': 'FR', 'region': 'Île-de-France',
            'city': 'Paris', 'zip': '75001', 'lat': 48.8534, 'lon': 2.3488,
            'isp': '', 'org': '', 'as': '',
        })
        self.assert_no_network()

    def test_is_foreign_ip_and_handoff_variant(self):
        self._with_reader({'198.51.100.7': PARIS_RECORD, '198.51.100.8': BIRMINGHAM_RECORD})
        self.assertEqual(geo_utils.is_foreign_ip('198.51.100.7')[0], True)
        self.assertEqual(geo_utils.is_foreign_ip('198.51.100.8')[0], False)
        foreign, geo = geo_utils.cached_is_foreign_ip('198.51.100.7')
        self.assertTrue(foreign)
        self.assertEqual(geo['city'], 'Paris')
        self.assert_no_network()

    def test_get_geolocation_from_ip_maps_lat_lon(self):
        self._with_reader({'198.51.100.8': BIRMINGHAM_RECORD})
        out = get_geolocation_from_ip('198.51.100.8')
        self.assertEqual(out, {
            'country': 'United States', 'city': 'Birmingham', 'region': 'Alabama',
            'latitude': 33.52, 'longitude': -86.80,
        })
        self.assert_no_network()

    def test_unknown_malformed_and_private_ips_are_empty(self):
        reader = self._with_reader({})
        self.assertEqual(geo_utils.get_ip_geo('198.51.100.99'), {})  # not in DB
        self.assertEqual(geo_utils.get_ip_geo('not-an-ip'), {})
        for ip in ('10.0.0.1', '172.16.4.4', '192.168.1.1', '127.0.0.1', '::1',
                   'unknown', '', None):
            self.assertEqual(geo_utils.get_ip_geo(ip), {}, ip)
        self.assertEqual(geo_utils.is_foreign_ip('10.0.0.1'), (False, {}))
        # Private addresses never reach the reader at all.
        self.assertEqual(reader.calls, ['198.51.100.99', 'not-an-ip'])
        self.assert_no_network()

    def test_country_falls_back_to_registered_country(self):
        self._with_reader({'198.51.100.9': {
            'registered_country': {'iso_code': 'DE', 'names': {'en': 'Germany'}}}})
        geo = geo_utils.get_ip_geo('198.51.100.9')
        self.assertEqual((geo['country_code'], geo['city'], geo['lat']), ('DE', '', None))

    def test_geo_utils_imports_no_network_client(self):
        import ast
        tree = ast.parse(open(geo_utils.__file__, encoding='utf-8').read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or '').split('.')[0])
        self.assertFalse(
            imported & {'requests', 'urllib', 'urllib3', 'http', 'httpx',
                        'aiohttp', 'socket', 'geoip2'},
            f'geo_utils imports a network client: {sorted(imported)}')
        self.assertFalse(hasattr(geo_utils, 'GEO_API_BASE_URL'))


@override_settings(GEOIP_CITY_DB=MISSING_DB)
class MissingDatabaseTests(_NoNetworkMixin, SimpleTestCase):

    def test_missing_db_returns_empty_and_warns_once(self):
        with self.assertLogs('src.geo_utils', level='WARNING') as logs:
            self.assertEqual(geo_utils.get_ip_geo('8.8.8.8'), {})
            self.assertEqual(geo_utils.is_foreign_ip('8.8.8.8'), (False, {}))
            geo_utils._next_check = 0.0  # force a re-check: still only one warning
            self.assertEqual(geo_utils.get_ip_geo('1.1.1.1'), {})
        self.assertEqual(len(logs.records), 1)
        self.assertIn(MISSING_DB, logs.output[0])
        self.assert_no_network()

    def test_missing_db_geolocation_is_blank_not_unknown(self):
        with self.assertLogs('src.geo_utils', level='WARNING'):
            out = get_geolocation_from_ip('8.8.8.8')
        self.assertEqual(out, {'country': '', 'city': '', 'region': '',
                               'latitude': None, 'longitude': None})
        self.assert_no_network()

    @override_settings(GEOIP_CITY_DB=__file__)
    def test_unreadable_db_returns_empty(self):
        # An existing file that is not an .mmdb: opening fails, lookups are {}.
        with self.assertLogs('src.geo_utils', level='WARNING') as logs:
            self.assertEqual(geo_utils.get_ip_geo('8.8.8.8'), {})
        self.assertIn('could not be opened', logs.output[0])
        self.assert_no_network()


@override_settings(GEOIP_CITY_DB=MISSING_DB)
class LoginWithoutDatabaseTests(_NoNetworkMixin, TestCase):
    """End to end, nothing mocked in the geo path: the login must succeed."""

    def test_login_succeeds_and_records_blank_location(self):
        user = ParliamentUser.objects.create_user(
            user_id='9431', name='Geo Tester', username='geotester',
            member_type='Member')
        user.username = 'geotester'
        user.set_password('testpass123')
        user.save()
        with self.assertLogs('src.geo_utils', level='WARNING'):
            resp = Client().post(
                reverse('login'), {'username': 'geotester', 'password': 'testpass123'},
                REMOTE_ADDR='203.0.113.10', HTTP_USER_AGENT='GeoTest/1.0')
        self.assertEqual(resp.status_code, 302)
        row = LoginHistory.objects.get(user=user)
        self.assertEqual((row.country, row.city, row.latitude), ('', '', None))
        self.assert_no_network()


class FastPathTests(SimpleTestCase):
    """get_geolocation_from_ip answers missing/private IPs without a lookup."""

    def test_fast_paths_do_not_delegate(self):
        with mock.patch('src.utils.security_utils.get_ip_geo') as g:
            self.assertEqual(get_geolocation_from_ip(None)['country'], 'Unknown')
            self.assertEqual(get_geolocation_from_ip('unknown')['country'], 'Unknown')
            self.assertEqual(
                get_geolocation_from_ip('192.168.1.1')['country'], 'Local Network')
            g.assert_not_called()

