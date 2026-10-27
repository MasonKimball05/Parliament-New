"""
Admin v2: Backups and Maintenance (v3.58.1).

Backups: the nightly timer (backup_database.sh / backup_db) writes pg_dump
archives, and verify_backup restores the newest one into a throwaway database
and records BACKUP_VERIFIED / BACKUP_VERIFY_FAILED in SecurityNotificationLog.
This page reads those rows and lists the backup folder (names, sizes, dates
only; the archives are 0600 root files and are never opened here). It doesn't
run backups: those need root and pg_dump, and belong to the server's timers.

Maintenance: maintenance mode, lockdown, and every feature flag and page
switch on one page, using the dashboard's existing toggle views.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.cache import cache
from django.shortcuts import render
from django.utils import timezone

from src.models import SecurityNotificationLog, SystemLockdown
from src.models_feature_flags import FeatureFlag, PageToggle
from src.view.admin_v2 import require_admin_v2_auth

STALE_AFTER = timedelta(hours=48)  # same limit as verify_backup --max-age-hours
LIST_LIMIT = 15


def backup_files(directory):
    """(files newest first, error message or None). Never opens a file."""
    try:
        entries = [p for p in directory.iterdir() if p.is_file() and p.suffix == '.dump']
    except FileNotFoundError:
        return [], f'{directory} does not exist on this server.'
    except PermissionError:
        return [], f'The web server cannot read {directory} (backups are root-only).'
    files = []
    for p in entries:
        try:
            st = p.stat()
        except OSError:
            continue
        files.append({
            'name': p.name,
            'size': st.st_size,
            'modified': datetime.fromtimestamp(st.st_mtime, tz=dt_timezone.utc),
        })
    files.sort(key=lambda f: f['modified'], reverse=True)
    return files, None


@require_admin_v2_auth
def admin_v2_backups(request):
    from src.management.commands.verify_backup import backup_dir

    directory = backup_dir()
    files, error = backup_files(directory)
    newest = files[0] if files else None
    drills = SecurityNotificationLog.objects.filter(
        event_type__in=('BACKUP_VERIFIED', 'BACKUP_VERIFY_FAILED')).order_by('-sent_at')
    last_ok = drills.filter(event_type='BACKUP_VERIFIED').first()
    last_fail = drills.filter(event_type='BACKUP_VERIFY_FAILED').first()
    context = {
        'directory': directory,
        'error': error,
        'files': files[:LIST_LIMIT],
        'file_count': len(files),
        'newest': newest,
        'stale': bool(newest) and timezone.now() - newest['modified'] > STALE_AFTER,
        'last_ok': last_ok,
        'last_fail': last_fail,
        'failing': bool(last_fail and (not last_ok or last_fail.sent_at > last_ok.sent_at)),
        'drills': list(drills[:10]),
    }
    return render(request, 'admin_v2/backups.html', context)


@require_admin_v2_auth
def admin_v2_maintenance(request):
    flags = list(FeatureFlag.objects.order_by('category', 'display_name'))
    context = {
        'maintenance': next((f for f in flags if f.name == 'maintenance_mode'), None),
        'blocked': cache.get('maintenance_blocked_count', 0),
        'lockdown': SystemLockdown.get_instance(),
        'flags': [f for f in flags if f.name != 'maintenance_mode'],
        'pages': list(PageToggle.objects.order_by('display_name')),
    }
    return render(request, 'admin_v2/maintenance.html', context)
