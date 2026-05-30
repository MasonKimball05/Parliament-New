"""
Automated PostgreSQL backup management command.

Usage:
    python manage.py backup_db              # Run backup + prune
    python manage.py backup_db --dry-run    # Show what would be pruned, no backup taken
    python manage.py backup_db --list       # List existing backups

Cron schedule (add to server crontab via `crontab -e`):
    # Weekly backup — Sunday 2am, Sept through May
    0 2 * 1-5,9-12 0 /path/to/.venv/bin/python /path/to/manage.py backup_db

    # Monthly backup — 1st of month, 2am, June through August
    0 2 1 6-8 * /path/to/.venv/bin/python /path/to/manage.py backup_db

Backup directory is read from the PARLIAMENT_BACKUP_DIR environment variable.
Falls back to /var/backups/parliament/ if not set.

Files are stored as parliament_YYYY-MM-DD_HHMMSS.dump using pg_dump custom
format (-Fc), which is compressed and restoreable with pg_restore.

Restore a backup:
    pg_restore -d <dbname> -U <user> -h <host> --clean /path/to/file.dump
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_BACKUP_DIR = '/var/backups/parliament/'
KEEP_BACKUPS = 12


class Command(BaseCommand):
    help = 'Back up the PostgreSQL database with pg_dump and prune old backups.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep',
            type=int,
            default=KEEP_BACKUPS,
            help=f'Number of backups to retain (default: {KEEP_BACKUPS}).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List what would be pruned without taking a backup or deleting anything.',
        )
        parser.add_argument(
            '--list',
            action='store_true',
            dest='list_backups',
            help='List existing backups and exit.',
        )

    def handle(self, *args, **options):
        backup_dir = Path(os.environ.get('PARLIAMENT_BACKUP_DIR', DEFAULT_BACKUP_DIR))
        keep = options['keep']
        dry_run = options['dry_run']
        list_backups = options['list_backups']

        # Ensure backup directory exists.
        backup_dir.mkdir(parents=True, exist_ok=True)

        if list_backups:
            self._list(backup_dir)
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no backup taken, no files deleted.'))
            self._show_prune_preview(backup_dir, keep)
            return

        # --- Take the backup ---
        db = self._get_db_settings()
        timestamp = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        filename = backup_dir / f'parliament_{timestamp}.dump'

        self.stdout.write(f'Backing up to {filename} …')
        self._run_pg_dump(db, filename)
        size_mb = filename.stat().st_size / (1024 * 1024)
        self.stdout.write(
            self.style.SUCCESS(f'Backup complete: {filename.name} ({size_mb:.1f} MB)')
        )

        # --- Prune old backups ---
        self._prune(backup_dir, keep)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_db_settings(self):
        db = settings.DATABASES.get('default', {})
        if db.get('ENGINE', '') != 'django.db.backends.postgresql':
            raise CommandError(
                f"backup_db only supports PostgreSQL. Current engine: {db.get('ENGINE')}"
            )
        return db

    def _run_pg_dump(self, db, output_path):
        env = os.environ.copy()
        if db.get('PASSWORD'):
            env['PGPASSWORD'] = db['PASSWORD']

        cmd = [
            'pg_dump',
            '--format=custom',   # compressed, supports parallel restore
            '--no-acl',
            '--no-owner',
        ]
        if db.get('HOST'):
            cmd += ['--host', db['HOST']]
        if db.get('PORT'):
            cmd += ['--port', str(db['PORT'])]
        if db.get('USER'):
            cmd += ['--username', db['USER']]
        cmd += ['--file', str(output_path)]
        cmd.append(db['NAME'])

        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode != 0:
            # Clean up partial file if dump failed.
            if output_path.exists():
                output_path.unlink()
            raise CommandError(f'pg_dump failed:\n{result.stderr}')

    def _existing_backups(self, backup_dir):
        """Return .dump files sorted oldest-first."""
        return sorted(backup_dir.glob('parliament_*.dump'))

    def _prune(self, backup_dir, keep):
        backups = self._existing_backups(backup_dir)
        to_delete = backups[:-keep] if len(backups) > keep else []
        if not to_delete:
            self.stdout.write(f'Retention: {len(backups)} backup(s) on disk, nothing to prune.')
            return
        for f in to_delete:
            f.unlink()
            self.stdout.write(f'  Pruned: {f.name}')
        self.stdout.write(
            self.style.SUCCESS(
                f'Pruned {len(to_delete)} old backup(s). {keep} most recent retained.'
            )
        )

    def _show_prune_preview(self, backup_dir, keep):
        backups = self._existing_backups(backup_dir)
        self.stdout.write(f'Found {len(backups)} backup(s) in {backup_dir}:')
        to_delete = set(backups[:-keep]) if len(backups) > keep else set()
        for f in backups:
            size_mb = f.stat().st_size / (1024 * 1024)
            tag = '  [WOULD DELETE]' if f in to_delete else '  [keep]'
            self.stdout.write(f'{tag}  {f.name}  ({size_mb:.1f} MB)')

    def _list(self, backup_dir):
        backups = self._existing_backups(backup_dir)
        if not backups:
            self.stdout.write('No backups found.')
            return
        self.stdout.write(f'{len(backups)} backup(s) in {backup_dir}:')
        for f in backups:
            size_mb = f.stat().st_size / (1024 * 1024)
            self.stdout.write(f'  {f.name}  ({size_mb:.1f} MB)')
