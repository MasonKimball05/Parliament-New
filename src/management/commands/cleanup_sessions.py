"""
Management command to clean up expired sessions and other stale data.
Run this periodically (daily recommended) to prevent database/memory bloat.

Usage:
    python manage.py cleanup_sessions

Recommended cron job:
    0 3 * * * cd /path/to/project && python manage.py cleanup_sessions
"""
from django.core.management.base import BaseCommand
from django.contrib.sessions.models import Session
from django.utils import timezone
import logging

logger = logging.getLogger('admin_actions')


class Command(BaseCommand):
    help = 'Clean up expired sessions and other stale data to prevent memory/database bloat'

    def handle(self, *args, **options):
        self.stdout.write('Starting cleanup...')

        # Clean up expired Django sessions
        expired_sessions = Session.objects.filter(expire_date__lt=timezone.now())
        session_count = expired_sessions.count()
        expired_sessions.delete()

        if session_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'Deleted {session_count} expired sessions')
            )
            logger.info(f'Cleanup: Deleted {session_count} expired sessions')
        else:
            self.stdout.write('No expired sessions to delete')

        # Clean up stale UserSession records
        # Note: production uses the cache session backend so the Django Session DB table
        # is empty — we cannot cross-reference session_key against it. Instead we use
        # two time/count-based strategies that work regardless of session backend:
        try:
            from django.conf import settings
            from src.models import UserSession

            # 1. Delete UserSessions inactive longer than SESSION_COOKIE_AGE (default 30 days).
            #    If the cookie is expired the session is definitely gone.
            max_age_seconds = getattr(settings, 'SESSION_COOKIE_AGE', 2592000)
            cutoff = timezone.now() - timezone.timedelta(seconds=max_age_seconds)
            expired_user_sessions = UserSession.objects.filter(last_activity__lt=cutoff)
            expired_count = expired_user_sessions.count()
            expired_user_sessions.delete()

            # 2. Per-user cap — keep only the 10 most recent sessions per user.
            #    Prevents accumulation even when sessions are still within the cookie window.
            keep_limit = 10
            per_user_capped = 0
            for user_id in UserSession.objects.values_list('user_id', flat=True).distinct():
                to_delete_ids = list(
                    UserSession.objects.filter(user_id=user_id)
                    .order_by('-last_activity')
                    .values_list('pk', flat=True)[keep_limit:]
                )
                if to_delete_ids:
                    deleted, _ = UserSession.objects.filter(pk__in=to_delete_ids).delete()
                    per_user_capped += deleted

            total = expired_count + per_user_capped
            if total > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Deleted {expired_count} expired + {per_user_capped} excess UserSession records'
                    )
                )
                logger.info(f'Cleanup: Deleted {total} stale UserSession records')
            else:
                self.stdout.write('No stale UserSession records to delete')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Could not clean UserSession records: {e}'))

        # Clean up old notification records (older than 90 days and read)
        try:
            from src.models import Notification
            cutoff = timezone.now() - timezone.timedelta(days=90)
            old_notifications = Notification.objects.filter(
                created_at__lt=cutoff,
                is_read=True
            )
            notif_count = old_notifications.count()
            old_notifications.delete()

            if notif_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {notif_count} old read notifications')
                )
                logger.info(f'Cleanup: Deleted {notif_count} old read notifications')
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clean notifications: {e}')
            )

        # Clean up old activity logs (older than 180 days)
        try:
            from src.models import ActivityLog
            cutoff = timezone.now() - timezone.timedelta(days=180)
            old_logs = ActivityLog.objects.filter(timestamp__lt=cutoff)
            log_count = old_logs.count()
            old_logs.delete()

            if log_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {log_count} old activity logs')
                )
                logger.info(f'Cleanup: Deleted {log_count} old activity logs')
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clean activity logs: {e}')
            )

        # Clean up old login history (older than 90 days)
        try:
            from src.models import LoginHistory
            cutoff = timezone.now() - timezone.timedelta(days=90)
            old_logins = LoginHistory.objects.filter(timestamp__lt=cutoff)
            login_count = old_logins.count()
            old_logins.delete()

            if login_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {login_count} old login history records')
                )
                logger.info(f'Cleanup: Deleted {login_count} old login history records')
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clean login history: {e}')
            )

        # Clean up resolved login alerts (older than 30 days)
        try:
            from src.models import LoginAlert
            cutoff = timezone.now() - timezone.timedelta(days=30)
            old_alerts = LoginAlert.objects.filter(
                created_at__lt=cutoff,
                status='resolved'
            )
            alert_count = old_alerts.count()
            old_alerts.delete()

            if alert_count > 0:
                self.stdout.write(
                    self.style.SUCCESS(f'Deleted {alert_count} old resolved login alerts')
                )
                logger.info(f'Cleanup: Deleted {alert_count} old resolved login alerts')
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clean login alerts: {e}')
            )

        # Clear performance middleware metrics
        try:
            from src.middleware.performance import clear_old_metrics
            clear_old_metrics()
            self.stdout.write(self.style.SUCCESS('Cleared old performance metrics'))
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clear performance metrics: {e}')
            )

        # Run garbage collection
        try:
            import gc
            collected = gc.collect()
            self.stdout.write(self.style.SUCCESS(f'Garbage collection freed {collected} objects'))
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not run garbage collection: {e}')
            )

        # Vacuum the database (PostgreSQL only)
        try:
            from django.db import connection
            if 'postgresql' in connection.vendor:
                # VACUUM can't run in a transaction, so we need autocommit
                with connection.cursor() as cursor:
                    old_autocommit = connection.get_autocommit()
                    connection.set_autocommit(True)
                    try:
                        cursor.execute('VACUUM ANALYZE')
                        self.stdout.write(self.style.SUCCESS('Database vacuumed and analyzed'))
                    finally:
                        connection.set_autocommit(old_autocommit)
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not vacuum database: {e}')
            )

        self.stdout.write(self.style.SUCCESS('Cleanup complete!'))
