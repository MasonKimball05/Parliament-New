"""
v3.38.0 (09-30-26): the backup restore drill (`manage.py verify_backup`).

The suite runs on SQLite, so the Postgres tools are mocked here. These tests
pin the drill's decisions: which archive, when to fail, that the throwaway
database is always dropped, and that a failure alerts while success only
logs. The real restore was verified by hand against Postgres 16 (see
changelogs/v3.38.0.md).

Run with: python manage.py test src.tests.infra.test_verify_backup
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from types import SimpleNamespace

from django.test import SimpleTestCase

from src.management.commands import verify_backup as vb

PG = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'parliament_db',
                  'USER': 'parl', 'PASSWORD': 'pw', 'HOST': 'localhost', 'PORT': '5432',
                  'OPTIONS': {'sslmode': 'prefer'}}}
TOC = '1; 0 0 TABLE DATA public src_parliamentuser parl\n2; 0 0 TABLE DATA public src_event parl\n'


def done(stdout='', rc=0, stderr=''):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class DrillTestBase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {'PARLIAMENT_BACKUP_DIR': self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)
        alert = mock.patch('src.security_notifications.send_security_alert')
        self.alert = alert.start()
        self.addCleanup(alert.stop)

    def dump(self, name, hours_old=1):
        p = self.dir / name
        p.write_bytes(b'PGDMP')
        t = time.time() - hours_old * 3600
        os.utime(p, (t, t))
        return p

    def call(self, *args):
        with mock.patch.object(vb, 'settings', SimpleNamespace(DATABASES=PG)):
            call_command('verify_backup', *args, stdout=open(os.devnull, 'w'),
                         stderr=open(os.devnull, 'w'))


class ArchiveSelectionTests(DrillTestBase):
    def test_newest_dump_wins_across_both_naming_schemes(self):
        self.dump('parliament_2026-09-01_020000.dump', hours_old=50)
        newest = self.dump('parliament_db_2026-09-30_033000.dump', hours_old=2)
        self.dump('notes.txt', hours_old=0)
        self.assertEqual(vb.newest_dump(self.dir), newest)

    def test_no_dumps_is_none(self):
        self.assertIsNone(vb.newest_dump(self.dir))


class FailureTests(DrillTestBase):
    def test_refuses_sqlite(self):
        sqlite = SimpleNamespace(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3'}})
        with mock.patch.object(vb, 'settings', sqlite):
            with self.assertRaisesMessage(CommandError, 'needs PostgreSQL'):
                call_command('verify_backup', stdout=open(os.devnull, 'w'))
        self.alert.assert_not_called()

    def test_empty_directory_fails_and_alerts(self):
        with self.assertRaisesMessage(CommandError, 'No *.dump files'):
            self.call()
        event, severity, _ = self.alert.call_args.args
        self.assertEqual((event, severity), ('BACKUP_VERIFY_FAILED', 'critical'))

    def test_stale_backup_fails(self):
        self.dump('parliament_db_old.dump', hours_old=72)
        with self.assertRaisesMessage(CommandError, 'backup timer may have stopped'):
            self.call()

    def test_no_alert_flag(self):
        with self.assertRaises(CommandError):
            self.call('--no-alert')
        self.alert.assert_not_called()

    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_unreadable_archive_fails(self, run):
        self.dump('parliament_db_x.dump')
        run.return_value = done(rc=1, stderr='pg_restore: error: could not read from input file: end of file')
        with self.assertRaisesMessage(CommandError, 'could not read from input file'):
            self.call('--list-only')

    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_archive_with_no_table_data_fails(self, run):
        self.dump('parliament_db_x.dump')
        run.return_value = done(stdout='; empty\n')
        with self.assertRaisesMessage(CommandError, 'no table data'):
            self.call('--list-only')


class RestoreTests(DrillTestBase):
    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_list_only_never_touches_databases(self, run):
        self.dump('parliament_db_x.dump')
        run.return_value = done(stdout=TOC)
        self.call('--list-only')
        tools = [c.args[0][0] for c in run.call_args_list]
        self.assertEqual(tools, ['pg_restore'])
        self.assertEqual(self.alert.call_args.args[:2], ('BACKUP_VERIFIED', 'low'))

    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_scratch_database_is_dropped_even_when_restore_fails(self, run):
        self.dump('parliament_db_x.dump')

        def fake(cmd, **kw):
            if cmd[0] == 'pg_restore' and '--list' in cmd:
                return done(stdout=TOC)
            if cmd[0] == 'pg_restore':
                return done(rc=1, stderr='pg_restore: error: could not read from input file: end of file')
            return done()
        run.side_effect = fake
        with self.assertRaisesMessage(CommandError, 'pg_restore failed'):
            self.call()
        calls = [c.args[0] for c in run.call_args_list]
        self.assertEqual(calls[-1][0], 'dropdb')
        self.assertEqual(calls[-1][-1], 'parliament_db_restore_check')
        # a stale copy from a crashed run is dropped before createdb, too
        tools = [c[0] for c in calls]
        self.assertLess(tools.index('dropdb'), tools.index('createdb'))

    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_restores_into_the_scratch_database_only(self, run):
        self.dump('parliament_db_x.dump')
        run.side_effect = lambda cmd, **kw: done(stdout=TOC if '--list' in cmd else '')
        with mock.patch.object(vb.Command, '_compare', return_value=['Row counts:']):
            self.call()
        restore = next(c.args[0] for c in run.call_args_list
                       if c.args[0][0] == 'pg_restore' and '--list' not in c.args[0])
        self.assertEqual(restore[restore.index('--dbname') + 1], 'parliament_db_restore_check')
        for flag in ('--no-owner', '--no-acl', '--exit-on-error', '--no-password'):
            self.assertIn(flag, restore)
        # the password travels in the environment, never on the command line
        self.assertNotIn('pw', restore)
        self.assertEqual(run.call_args_list[0].kwargs['env']['PGPASSWORD'], 'pw')

    @mock.patch('src.management.commands.verify_backup.subprocess.run')
    def test_createdb_permission_error_explains_the_fix(self, run):
        self.dump('parliament_db_x.dump')

        def fake(cmd, **kw):
            if cmd[0] == 'createdb':
                return done(rc=1, stderr='ERROR:  permission denied to create database')
            return done(stdout=TOC if '--list' in cmd else '')
        run.side_effect = fake
        with self.assertRaisesMessage(CommandError, 'CREATEDB'):
            self.call()

    def test_no_confidential_tables_are_counted(self):
        for label in vb.CHECK_MODELS:
            self.assertNotRegex(label.lower(), r'kai|slating|slate|vote|ballot')


class InstallFilesTests(SimpleTestCase):
    def test_service_can_find_the_postgres_tools(self):
        # The other units set PATH to the venv only; pg_restore lives in /usr/bin.
        root = Path(__file__).resolve().parents[3]
        unit = (root / 'parliament-backup-verify.service').read_text()
        self.assertIn(':/usr/bin', unit)
        self.assertIn('manage.py verify_backup', unit)
        self.assertTrue((root / 'parliament-backup-verify.timer').is_file())
