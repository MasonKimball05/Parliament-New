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

        # Clean up expired sessions
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

        # Clear the Django cache (helps with LocMemCache bloat)
        try:
            from django.core.cache import cache
            cache.clear()
            self.stdout.write(self.style.SUCCESS('Cleared Django cache'))
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Could not clear cache: {e}')
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
