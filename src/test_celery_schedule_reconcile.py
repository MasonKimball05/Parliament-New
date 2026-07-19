"""
v3.15.2 — orphan reconciliation in setup_celery_schedules.

This command can DELETE PeriodicTask rows, so these tests pin the exact
behavior: only rows whose task path is UNREGISTERED are flagged/pruned,
valid rows (managed or otherwise) are preserved, and a safety fuse prevents
deletion if the task registry failed to load.
"""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django_celery_beat.models import PeriodicTask, CrontabSchedule


class OrphanReconcileTests(TestCase):
    def setUp(self):
        self.ct, _ = CrontabSchedule.objects.get_or_create(minute='0', hour='9')

    def _run(self, *args):
        out = StringIO()
        call_command('setup_celery_schedules', *args, stdout=out)
        return out.getvalue()

    def test_dead_row_reported_not_deleted_without_flag(self):
        PeriodicTask.objects.create(
            name='Prune old auth tokens', task='tasks.prune_old_auth_tokens',
            crontab=self.ct, enabled=True)
        out = self._run()
        self.assertIn('Prune old auth tokens', out)
        self.assertIn('UNREGISTERED', out)
        # report-only: still present
        self.assertTrue(
            PeriodicTask.objects.filter(name='Prune old auth tokens').exists())

    def test_prune_deletes_only_dead_rows(self):
        PeriodicTask.objects.create(
            name='Prune old auth tokens', task='tasks.prune_old_auth_tokens',
            crontab=self.ct, enabled=True)
        # A row pointing at a REGISTERED task must survive even though it's not
        # in the managed SCHEDULES set (simulates the honeypot-digest case).
        PeriodicTask.objects.create(
            name='Some valid unmanaged task', task='tasks.send_daily_digest',
            crontab=self.ct, enabled=True)
        self._run('--prune-orphans')
        self.assertFalse(
            PeriodicTask.objects.filter(name='Prune old auth tokens').exists())
        self.assertTrue(
            PeriodicTask.objects.filter(name='Some valid unmanaged task').exists())
        # managed rows created by the command are intact
        self.assertTrue(
            PeriodicTask.objects.filter(name='Send daily site digest').exists())

    def test_safety_fuse_blocks_when_registry_unloaded(self):
        """If the sentinel managed task isn't registered, the command must
        touch nothing — guards against nuking live schedules on a bad load."""
        PeriodicTask.objects.create(
            name='Prune old auth tokens', task='tasks.prune_old_auth_tokens',
            crontab=self.ct, enabled=True)
        with mock.patch('celery.current_app.tasks', {}):
            out = self._run('--prune-orphans')
        self.assertIn('registry looks unloaded', out)
        self.assertTrue(
            PeriodicTask.objects.filter(name='Prune old auth tokens').exists())
