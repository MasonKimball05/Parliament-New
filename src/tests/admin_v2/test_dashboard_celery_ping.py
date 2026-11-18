"""The dashboard's Celery card doesn't cost every page load a second (11-18-26).

A broadcast ping waits out its whole timeout collecting replies, so the
dashboard took ~1 s even with a healthy worker. It now stops at the first
reply (limit=1) and keeps the answer for a minute.

    python manage.py test src.tests.admin_v2.test_dashboard_celery_ping
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from src.view import admin_v2


class DashboardCeleryPingTests(TestCase):

    def setUp(self):
        cache.delete(admin_v2.CELERY_WORKERS_UP_CACHE_KEY)
        self.addCleanup(cache.delete, admin_v2.CELERY_WORKERS_UP_CACHE_KEY)

    def test_stops_at_the_first_reply_and_caches_it(self):
        with mock.patch('celery.app.control.Control.inspect') as inspect:
            inspect.return_value.ping.return_value = {'worker@host': {'ok': 'pong'}}
            self.assertTrue(admin_v2.dashboard_celery_summary()['workers_up'])
            self.assertTrue(admin_v2.dashboard_celery_summary()['workers_up'])
        inspect.assert_called_once_with(timeout=1, limit=1)
        self.assertTrue(cache.get(admin_v2.CELERY_WORKERS_UP_CACHE_KEY))

    def test_no_reply_is_remembered_too(self):
        with mock.patch('celery.app.control.Control.inspect') as inspect:
            inspect.return_value.ping.return_value = None
            self.assertFalse(admin_v2.dashboard_celery_summary()['workers_up'])
            self.assertFalse(admin_v2.dashboard_celery_summary()['workers_up'])
        inspect.assert_called_once()

    def test_a_broker_error_is_not_cached(self):
        with mock.patch('celery.app.control.Control.inspect', side_effect=OSError('broker down')):
            self.assertFalse(admin_v2.dashboard_celery_summary()['workers_up'])
        self.assertIsNone(cache.get(admin_v2.CELERY_WORKERS_UP_CACHE_KEY))
