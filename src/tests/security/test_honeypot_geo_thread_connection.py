"""
v3.51.3 — the honeypot's geolocation thread closes its database connection.

`honeypot()` starts a plain `threading.Thread` for `lookup_geo`, which reads
and saves the `HoneypotAccess` row. Django opens a connection per thread and
only closes them at the end of a request — this thread is not one, so every
honeypot hit left one idle PostgreSQL backend behind in Daphne until the next
restart. Seen on prod 10-21-26: 6 idle `parliament_user` connections, 3–9 hours
old, with `CONN_MAX_AGE=0` (which should leave none).

Run with: python manage.py test src.tests.security.test_honeypot_geo_thread_connection
"""
import threading
from unittest import mock

from django.db import connection
from django.test import SimpleTestCase, TransactionTestCase, skipUnlessDBFeature

from src.models import HoneypotAccess
from src.view import honeypot


class GeoThreadClosesConnectionTests(SimpleTestCase):
    def test_target_closes_connections_even_when_lookup_raises(self):
        with mock.patch.object(honeypot, 'lookup_geo', side_effect=RuntimeError('boom')), \
                mock.patch('src.view.honeypot.connections') as conns:
            t = threading.Thread(target=honeypot._lookup_geo_in_thread, args=('203.0.113.9', 1))
            t.start(); t.join()
        conns.close_all.assert_called_once_with()

    def test_honeypot_starts_the_closing_wrapper(self):
        src = open(honeypot.__file__, encoding='utf-8').read()
        self.assertIn('target=_lookup_geo_in_thread', src)
        self.assertNotIn('target=lookup_geo,', src)


class NoBackendLeftBehindTests(TransactionTestCase):
    """End to end on PostgreSQL: count backends before and after the thread."""

    def _backends(self):
        with connection.cursor() as c:
            c.execute('SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()')
            return c.fetchone()[0]

    def test_thread_leaves_no_idle_connection(self):
        if connection.vendor != 'postgresql':
            self.skipTest('needs PostgreSQL (pg_stat_activity)')
        rec = HoneypotAccess.objects.create(endpoint='/.env', ip_address='203.0.113.9',
                                            request_method='GET', action_taken='blocked',
                                            additional_data={})
        before = self._backends()
        with mock.patch.object(honeypot, 'get_ip_geo', return_value={'country': 'X'}):
            t = threading.Thread(target=honeypot._lookup_geo_in_thread, args=('203.0.113.9', rec.id))
            t.start(); t.join()
        rec.refresh_from_db()
        self.assertEqual(rec.additional_data.get('geo'), {'country': 'X'})
        self.assertEqual(self._backends(), before)
