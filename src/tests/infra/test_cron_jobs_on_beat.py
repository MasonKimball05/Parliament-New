"""
11-10-26: two jobs only ran from management commands that needed cron
entries the server never had (no crontab for www-data; root's had neither):
scheduled maintenance never started on its own and the honeypot digest was
never sent. Both are Celery beat tasks now. An old maintenance window that
never fired is skipped, not started the moment the job first runs.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from src.models_feature_flags import FeatureFlag, ScheduledMaintenance


def window(start_offset, minutes=60, **kw):
    return ScheduledMaintenance.objects.create(
        title='Upgrade', message='Back soon', scheduled_start=timezone.now() + start_offset,
        estimated_duration_minutes=minutes, **kw)


class ScheduledMaintenanceTaskTests(TestCase):

    def run_task(self):
        from src.tasks import start_scheduled_maintenance
        start_scheduled_maintenance()

    def mode_on(self):
        return FeatureFlag.objects.filter(name='maintenance_mode', is_enabled=True).exists()

    def test_starts_a_window_whose_time_has_come(self):
        m = window(-timedelta(minutes=1))
        self.run_task()
        m.refresh_from_db()
        self.assertTrue(m.maintenance_started)
        self.assertTrue(self.mode_on())

    def test_future_window_waits(self):
        window(timedelta(hours=2))
        self.run_task()
        self.assertFalse(self.mode_on())

    def test_a_window_that_already_ended_is_skipped(self):
        old = window(-timedelta(days=30), minutes=60)
        self.run_task()
        old.refresh_from_db()
        self.assertFalse(old.maintenance_started)
        self.assertFalse(self.mode_on())
        # ...but a current one behind it still starts.
        now = window(-timedelta(minutes=5), minutes=60)
        self.run_task()
        now.refresh_from_db()
        self.assertTrue(now.maintenance_started)

    def test_cancelled_window_is_ignored(self):
        window(-timedelta(minutes=1), is_active=False)
        self.run_task()
        self.assertFalse(self.mode_on())


class HoneypotDigestTaskTests(TestCase):

    def test_calls_the_digest(self):
        from src.tasks import send_honeypot_digest_task
        with mock.patch('src.security_notifications.send_honeypot_digest', return_value=False) as digest:
            send_honeypot_digest_task()
        digest.assert_called_once_with()


class BeatRegistrationTests(TestCase):

    def test_both_registered(self):
        from src.management.commands.setup_celery_schedules import SCHEDULES
        tasks = {s['task']: s for s in SCHEDULES}
        self.assertIn('tasks.start_scheduled_maintenance', tasks)
        self.assertEqual(tasks['tasks.send_honeypot_digest']['crontab'], {'hour': '8', 'minute': '0'})
