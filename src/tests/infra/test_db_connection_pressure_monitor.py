"""
v3.31.x — the DB connection-pressure monitor (src/tasks/db_health.py), added
after the 09-16-26 live incident where a burst of pledge-onboarding logins
exhausted Postgres's max_connections and 500'd most of the site.

Covers: no-ops entirely on sqlite (the test backend, and prod's Postgres-only
condition); fires an alert when usage is at/above WARNING_THRESHOLD and not
below it; the cooldown suppresses a repeat alert within the window; a query
failure (Postgres already refusing connections — the exact scenario this
task exists to warn about in advance) is caught rather than raised; and the
task is registered so Celery's autodiscovery / the orphan-reconciliation
sentinel in setup_celery_schedules.py can see it.
"""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from src.tasks.db_health import monitor_db_connection_pressure, WARNING_THRESHOLD


class FakeCursor:
    """
    Minimal cursor stand-in returning canned rows for the task's three
    sequential queries, in the order it issues them: total connections,
    max_connections, then the per-state breakdown.
    """
    def __init__(self, total, max_connections, by_state):
        self._results = [(total,), (max_connections,), by_state]
        self._next = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        self._next = self._results.pop(0)

    def fetchone(self):
        return self._next

    def fetchall(self):
        return self._next


def patched_connection(total, max_connections, by_state=(('active', 1),)):
    cursor = FakeCursor(total, max_connections, by_state)
    conn = mock.MagicMock()
    conn.vendor = 'postgresql'
    conn.cursor.return_value = cursor
    return conn


class NoOpsOnSqliteTests(TestCase):
    def test_does_nothing_on_the_sqlite_test_backend(self):
        # No patching at all — this runs against the real test connection,
        # which is sqlite. If this task ever queried it directly, it would
        # raise (sqlite has no pg_stat_activity); reaching the end cleanly
        # is the assertion.
        monitor_db_connection_pressure()

    @mock.patch('src.tasks.email.send_security_alert_task.delay')
    def test_never_reaches_the_alert_path_on_sqlite(self, mock_delay):
        monitor_db_connection_pressure()
        mock_delay.assert_not_called()


class ThresholdBehaviorTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch('src.tasks.email.send_security_alert_task.delay')
    @mock.patch('src.tasks.db_health.connection')
    def test_fires_an_alert_at_or_above_the_threshold(self, mock_conn, mock_delay):
        total = int(100 * WARNING_THRESHOLD)
        mock_conn.vendor = 'postgresql'
        mock_conn.cursor.return_value = FakeCursor(total, 100, (('idle', total),))
        monitor_db_connection_pressure()
        mock_delay.assert_called_once()
        kwargs = mock_delay.call_args.kwargs
        self.assertEqual(kwargs['event_type'], 'db_connection_pressure')
        self.assertEqual(kwargs['severity'], 'high')
        self.assertTrue(kwargs['force_send'])
        self.assertIn(f'{total}/100', kwargs['details'])

    @mock.patch('src.tasks.email.send_security_alert_task.delay')
    @mock.patch('src.tasks.db_health.connection')
    def test_does_not_fire_below_the_threshold(self, mock_conn, mock_delay):
        total = int(100 * WARNING_THRESHOLD) - 5
        mock_conn.vendor = 'postgresql'
        mock_conn.cursor.return_value = FakeCursor(total, 100, (('idle', total),))
        monitor_db_connection_pressure()
        mock_delay.assert_not_called()

    @mock.patch('src.tasks.email.send_security_alert_task.delay')
    @mock.patch('src.tasks.db_health.connection')
    def test_a_second_call_within_the_cooldown_does_not_re_alert(self, mock_conn, mock_delay):
        total = 90
        mock_conn.vendor = 'postgresql'
        mock_conn.cursor.return_value = FakeCursor(total, 100, (('idle', total),))
        monitor_db_connection_pressure()
        mock_conn.cursor.return_value = FakeCursor(total, 100, (('idle', total),))
        monitor_db_connection_pressure()
        self.assertEqual(mock_delay.call_count, 1)

    @mock.patch('src.tasks.email.send_security_alert_task.delay')
    @mock.patch('src.tasks.db_health.connection')
    def test_a_query_failure_is_caught_not_raised(self, mock_conn, mock_delay):
        # This is the scenario the task exists to give advance warning of —
        # Postgres already refusing connections. The task's own query hitting
        # that same wall must not crash the beat worker.
        mock_conn.vendor = 'postgresql'
        mock_conn.cursor.side_effect = Exception(
            'FATAL: remaining connection slots are reserved for roles with '
            'the SUPERUSER attribute'
        )
        monitor_db_connection_pressure()  # must not raise
        mock_delay.assert_not_called()


class TaskRegistrationTests(TestCase):
    def test_the_task_is_registered_with_celery(self):
        from celery import current_app
        current_app.loader.import_default_modules()
        self.assertIn('tasks.monitor_db_connection_pressure', current_app.tasks)

    def test_is_exported_from_the_tasks_package(self):
        from src import tasks
        self.assertIn('monitor_db_connection_pressure', tasks.__all__)
        self.assertTrue(hasattr(tasks, 'monitor_db_connection_pressure'))

    def test_is_wired_into_setup_celery_schedules(self):
        from src.management.commands.setup_celery_schedules import SCHEDULES
        names = {spec['task'] for spec in SCHEDULES}
        self.assertIn('tasks.monitor_db_connection_pressure', names)
