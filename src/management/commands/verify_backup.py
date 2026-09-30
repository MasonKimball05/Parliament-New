"""
Prove the newest database backup actually restores (v3.38.0, 09-30-26).

WHY: `backup_database.sh` (the nightly systemd timer) and `backup_db` both
write `pg_dump -Fc` archives, and nothing has ever checked one. A backup that
has never been restored is a hope, not a backup. The ways this goes wrong are
all silent: the timer stops firing, pg_dump writes a truncated file after the
disk fills, a dump is from the wrong database, or the archive is fine but
won't restore into the current Postgres.

WHAT, in order (each step stops the drill on failure):

  1. FRESH   the newest `*.dump` in the backup directory is younger than
             --max-age-hours (default 48). Catches a backup timer that died.
  2. READABLE  `pg_restore --list` reads the archive's table of contents.
  3. RESTORES  the archive is restored with `--exit-on-error` into a
             throwaway database, `<DB_NAME>_restore_check`, which is always
             dropped afterwards (and before, in case a previous run died).
  4. PLAUSIBLE  row counts of a few core tables in the restored copy are at
             least --min-ratio (default 0.5) of the live counts, and every
             migration recorded in the copy is one the live database knows.
             Catches an empty dump or a dump of a different database.

Step 3 is skipped with --list-only (for a database role without CREATEDB).

On FAILURE it sends a critical security alert (emailed to
SECURITY_ALERT_EMAIL, logged in SecurityNotificationLog) and exits non-zero,
so systemd also records the failure. On SUCCESS it logs a low-severity
`BACKUP_VERIFIED` row (no email), so the admin security log shows when the
last good drill ran.

CONFIDENTIALITY: the drill only COUNTS rows, and never in Kai, Slating or
vote tables. Nothing from the restored copy is printed except those counts and
migration names. The copy is dropped in a `finally`.

Run it as the user that can read the backups (the nightly script writes them
0600 as root). Install: see `parliament-backup-verify.service`/`.timer`.

    python manage.py verify_backup                      # newest dump, full drill
    python manage.py verify_backup --file /path/x.dump  # a specific archive
    python manage.py verify_backup --list-only          # steps 1, 2 only
    python manage.py verify_backup --no-alert           # don't email on failure

The database role needs CREATEDB for step 3:
    ALTER ROLE <DB_USER> CREATEDB;
"""
import os
import subprocess
import time
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

DEFAULT_BACKUP_DIR = '/var/backups/parliament'
SCRATCH_SUFFIX = '_restore_check'
PG_TIMEOUT_SECONDS = 1800

# Core, non-confidential tables whose counts show the dump is this database
# and isn't empty. Deliberately no Kai / Slating / Vote tables.
CHECK_MODELS = (
    'src.ParliamentUser', 'src.Role', 'src.Committee', 'src.Event',
    'src.Attendance', 'src.Legislation', 'src.ChapterMinutes', 'src.Announcement',
)


class DrillFailed(Exception):
    """A step failed. The message is safe to email (counts and names only)."""


def backup_dir():
    return Path(os.environ.get('PARLIAMENT_BACKUP_DIR')
                or os.environ.get('BACKUP_DIR') or DEFAULT_BACKUP_DIR)


def newest_dump(directory):
    """Newest `*.dump` by mtime. Both backup_database.sh (`<db>_…`) and
    backup_db (`parliament_…`) write here with different prefixes."""
    dumps = [p for p in Path(directory).glob('*.dump') if p.is_file()]
    return max(dumps, key=lambda p: p.stat().st_mtime) if dumps else None


class Command(BaseCommand):
    help = 'Restore the newest database backup into a throwaway database and check it.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='Archive to check (default: newest *.dump in the backup dir).')
        parser.add_argument('--max-age-hours', type=float, default=48)
        parser.add_argument('--min-ratio', type=float, default=0.5,
                            help='Restored row count must be at least this fraction of live (default 0.5).')
        parser.add_argument('--list-only', action='store_true',
                            help='Only check freshness and that the archive is readable; no restore.')
        parser.add_argument('--no-alert', action='store_true', help='Do not send an alert on failure.')

    # ------------------------------------------------------------------

    def handle(self, *args, **opts):
        db = settings.DATABASES['default']
        if db.get('ENGINE') != 'django.db.backends.postgresql':
            raise CommandError(f"verify_backup needs PostgreSQL, not {db.get('ENGINE')}.")
        self.db = db
        started = time.monotonic()
        try:
            summary = self._drill(opts)
        except DrillFailed as e:
            if not opts['no_alert']:
                self._alert('BACKUP_VERIFY_FAILED', 'critical', str(e))
            raise CommandError(f'Backup drill FAILED: {e}')
        summary += f'\nDrill took {time.monotonic() - started:.0f}s.'
        self._alert('BACKUP_VERIFIED', 'low', summary)
        self.stdout.write(self.style.SUCCESS(summary))

    def _drill(self, opts):
        # 1. FRESH
        if opts['file']:
            dump = Path(opts['file'])
            if not dump.is_file():
                raise DrillFailed(f'{dump} does not exist.')
        else:
            dump = newest_dump(backup_dir())
            if dump is None:
                raise DrillFailed(f'No *.dump files in {backup_dir()}. Are backups running, '
                                  'and can this user read that directory?')
        age_h = (time.time() - dump.stat().st_mtime) / 3600
        if not opts['file'] and age_h > opts['max_age_hours']:
            raise DrillFailed(f'Newest backup {dump.name} is {age_h:.0f}h old '
                              f"(limit {opts['max_age_hours']:.0f}h). The backup timer may have stopped.")
        size_mb = dump.stat().st_size / (1024 * 1024)
        lines = [f'Archive: {dump.name} ({size_mb:.1f} MB, {age_h:.0f}h old)']

        # 2. READABLE
        toc = self._run(['pg_restore', '--list', str(dump)], 'pg_restore --list')
        data_entries = sum(1 for line in toc.splitlines() if ' TABLE DATA ' in line)
        if data_entries == 0:
            raise DrillFailed(f'{dump.name} is readable but contains no table data.')
        lines.append(f'Readable: {data_entries} table-data entries.')
        if opts['list_only']:
            return '\n'.join(lines + ['Restore skipped (--list-only).'])

        # 3. RESTORES + 4. PLAUSIBLE
        scratch = f"{self.db['NAME']}{SCRATCH_SUFFIX}"
        self._run(self._conn_args('dropdb', '--if-exists', scratch), 'dropdb (stale copy)')
        self._run(self._conn_args('createdb', scratch), 'createdb',
                  hint='The database role needs CREATEDB: ALTER ROLE <DB_USER> CREATEDB; '
                       '(or run with --list-only).')
        try:
            self._run(self._conn_args('pg_restore', '--no-owner', '--no-acl', '--exit-on-error',
                                      '--dbname', scratch, str(dump)), 'pg_restore')
            lines.append(f'Restored into {scratch}.')
            lines += self._compare(scratch, opts['min_ratio'])
        finally:
            self._run(self._conn_args('dropdb', '--if-exists', scratch), 'dropdb', check=False)
        return '\n'.join(lines)

    # ------------------------------------------------------------------

    def _compare(self, scratch, min_ratio):
        import psycopg2
        copy = psycopg2.connect(dbname=scratch, user=self.db.get('USER') or None,
                                password=self.db.get('PASSWORD') or None,
                                host=self.db.get('HOST') or None, port=self.db.get('PORT') or None,
                                sslmode=self.db.get('OPTIONS', {}).get('sslmode', 'prefer'),
                                connect_timeout=10)
        lines, problems = [], []
        try:
            with copy.cursor() as c_copy, connection.cursor() as c_live:
                for label in CHECK_MODELS:
                    try:
                        table = apps.get_model(label)._meta.db_table
                    except LookupError:
                        continue
                    q = f'SELECT COUNT(*) FROM "{table}"'   # table name from our own models
                    c_live.execute(q)
                    live = c_live.fetchone()[0]
                    try:
                        c_copy.execute(q)
                        restored = c_copy.fetchone()[0]
                    except psycopg2.Error:
                        copy.rollback()
                        problems.append(f'{table}: missing from the backup')
                        continue
                    lines.append(f'  {table}: {restored} restored / {live} live')
                    if live and restored < live * min_ratio:
                        problems.append(f'{table}: {restored} rows restored vs {live} live '
                                        f'(below {min_ratio:.0%})')

                c_live.execute('SELECT app, name FROM django_migrations')
                live_migrations = set(c_live.fetchall())
                c_copy.execute('SELECT app, name FROM django_migrations')
                copy_migrations = set(c_copy.fetchall())
        finally:
            copy.close()

        if not copy_migrations:
            problems.append('the backup has no django_migrations rows')
        unknown = sorted(f'{a}.{n}' for a, n in copy_migrations - live_migrations)
        if unknown:
            problems.append('the backup has migrations this database never applied '
                            f"(a different database?): {', '.join(unknown[:5])}")
        behind = len({m for m in live_migrations if m[0] == 'src'} - copy_migrations)
        lines.append(f'Migrations: {len(copy_migrations)} in backup, {behind} newer src migration(s) since.')
        if problems:
            raise DrillFailed('Restored, but the copy looks wrong:\n  - ' + '\n  - '.join(problems)
                              + '\n' + '\n'.join(lines))
        return ['Row counts:'] + lines

    def _conn_args(self, tool, *args):
        cmd = [tool]
        if self.db.get('HOST'):
            cmd += ['--host', self.db['HOST']]
        if self.db.get('PORT'):
            cmd += ['--port', str(self.db['PORT'])]
        if self.db.get('USER'):
            cmd += ['--username', self.db['USER']]
        cmd.append('--no-password')
        return cmd + list(args)

    def _run(self, cmd, what, hint='', check=True):
        env = os.environ.copy()
        if self.db.get('PASSWORD'):
            env['PGPASSWORD'] = self.db['PASSWORD']
        env.setdefault('PGSSLMODE', self.db.get('OPTIONS', {}).get('sslmode', 'prefer'))
        try:
            result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                    timeout=PG_TIMEOUT_SECONDS)
        except FileNotFoundError:
            raise DrillFailed(f'{cmd[0]} is not installed or not on PATH.')
        except subprocess.TimeoutExpired:
            raise DrillFailed(f'{what} took longer than {PG_TIMEOUT_SECONDS // 60} minutes.')
        if check and result.returncode != 0:
            # stderr from pg tools names objects and errors, not row data.
            err = result.stderr.strip()[-1500:]
            raise DrillFailed(f'{what} failed: {err}' + (f'\n{hint}' if hint else ''))
        return result.stdout

    def _alert(self, event_type, severity, details):
        try:
            from src.security_notifications import send_security_alert
            send_security_alert(event_type, severity, details)
        except Exception as e:   # the drill's verdict must not depend on the mailer
            self.stderr.write(f'Could not record/send the {event_type} alert: {e}')
