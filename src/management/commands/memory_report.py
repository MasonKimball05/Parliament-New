"""
Management command to generate a memory usage report.
Useful for diagnosing memory leaks and identifying optimization opportunities.

Usage:
    python manage.py memory_report
    python manage.py memory_report --detailed
"""
import os
import gc
import sys
from django.core.management.base import BaseCommand
from django.db import connection
from django.core.cache import cache
from django.conf import settings


class Command(BaseCommand):
    help = 'Generate a memory usage report for the Parliament application'

    def add_arguments(self, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Show detailed memory breakdown',
        )
        parser.add_argument(
            '--gc',
            action='store_true',
            help='Run garbage collection before reporting',
        )

    def handle(self, *args, **options):
        if options['gc']:
            self.stdout.write('Running garbage collection...')
            gc.collect()

        self.stdout.write(self.style.SUCCESS('\n=== Parliament Memory Report ===\n'))

        # 1. Process Memory Usage
        self._report_process_memory()

        # 2. Database Statistics
        self._report_database_stats()

        # 3. Cache Statistics
        self._report_cache_stats()

        # 4. Session Statistics
        self._report_session_stats()

        # 5. Log File Sizes
        self._report_log_sizes()

        # 6. Performance Middleware
        self._report_performance_middleware()

        # 7. Garbage Collection Stats
        if options['detailed']:
            self._report_gc_stats()
            self._report_large_objects()

        self.stdout.write(self.style.SUCCESS('\n=== Recommendations ===\n'))
        self._generate_recommendations()

    def _report_process_memory(self):
        """Report current process memory usage"""
        self.stdout.write(self.style.HTTP_INFO('1. Process Memory:'))

        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # maxrss is in KB on Linux, bytes on macOS
            max_rss_mb = usage.ru_maxrss / 1024  # Convert to MB (Linux)
            if sys.platform == 'darwin':
                max_rss_mb = usage.ru_maxrss / (1024 * 1024)  # macOS is in bytes

            self.stdout.write(f'   Max RSS: {max_rss_mb:.1f} MB')
            self.stdout.write(f'   User time: {usage.ru_utime:.2f}s')
            self.stdout.write(f'   System time: {usage.ru_stime:.2f}s')
        except ImportError:
            self.stdout.write('   (resource module not available)')

        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            self.stdout.write(f'   RSS: {mem_info.rss / (1024*1024):.1f} MB')
            self.stdout.write(f'   VMS: {mem_info.vms / (1024*1024):.1f} MB')

            # System memory
            sys_mem = psutil.virtual_memory()
            self.stdout.write(f'   System total: {sys_mem.total / (1024*1024*1024):.1f} GB')
            self.stdout.write(f'   System available: {sys_mem.available / (1024*1024*1024):.1f} GB')
            self.stdout.write(f'   System used: {sys_mem.percent}%')
        except ImportError:
            self.stdout.write('   (install psutil for detailed memory info: pip install psutil)')

        self.stdout.write('')

    def _report_database_stats(self):
        """Report database connection and table stats"""
        self.stdout.write(self.style.HTTP_INFO('2. Database Statistics:'))

        db_settings = settings.DATABASES['default']
        self.stdout.write(f'   Engine: {db_settings["ENGINE"].split(".")[-1]}')
        self.stdout.write(f'   CONN_MAX_AGE: {db_settings.get("CONN_MAX_AGE", 0)}s')

        # Table sizes (PostgreSQL specific)
        if 'postgresql' in db_settings['ENGINE']:
            try:
                with connection.cursor() as cursor:
                    # Get table sizes
                    cursor.execute("""
                        SELECT relname as table,
                               pg_size_pretty(pg_total_relation_size(relid)) as size
                        FROM pg_catalog.pg_statio_user_tables
                        ORDER BY pg_total_relation_size(relid) DESC
                        LIMIT 10
                    """)
                    rows = cursor.fetchall()
                    self.stdout.write('   Top 10 tables by size:')
                    for table, size in rows:
                        self.stdout.write(f'      {table}: {size}')

                    # Connection count
                    cursor.execute("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = current_database()
                    """)
                    conn_count = cursor.fetchone()[0]
                    self.stdout.write(f'   Active connections: {conn_count}')
            except Exception as e:
                self.stdout.write(f'   Error getting DB stats: {e}')

        self.stdout.write('')

    def _report_cache_stats(self):
        """Report cache statistics"""
        self.stdout.write(self.style.HTTP_INFO('3. Cache Statistics:'))

        cache_backend = settings.CACHES['default']['BACKEND']
        self.stdout.write(f'   Backend: {cache_backend.split(".")[-1]}')

        if 'redis' in cache_backend.lower():
            try:
                # Get Redis info
                from django_redis import get_redis_connection
                redis_conn = get_redis_connection('default')
                info = redis_conn.info('memory')
                self.stdout.write(f'   Redis memory used: {info.get("used_memory_human", "N/A")}')
                self.stdout.write(f'   Redis memory peak: {info.get("used_memory_peak_human", "N/A")}')

                # Key count
                key_count = redis_conn.dbsize()
                self.stdout.write(f'   Redis keys: {key_count}')
            except Exception as e:
                self.stdout.write(f'   Redis info error: {e}')
        elif 'locmem' in cache_backend.lower():
            self.stdout.write('   Using in-memory cache (LocMemCache)')
            max_entries = settings.CACHES['default'].get('OPTIONS', {}).get('MAX_ENTRIES', 300)
            self.stdout.write(f'   Max entries: {max_entries}')
            self.stdout.write(self.style.WARNING('   Warning: LocMemCache is per-process, consider Redis'))

        self.stdout.write('')

    def _report_session_stats(self):
        """Report session statistics"""
        self.stdout.write(self.style.HTTP_INFO('4. Session Statistics:'))

        try:
            from django.contrib.sessions.models import Session
            from django.utils import timezone

            total_sessions = Session.objects.count()
            expired_sessions = Session.objects.filter(expire_date__lt=timezone.now()).count()
            active_sessions = total_sessions - expired_sessions

            self.stdout.write(f'   Total sessions: {total_sessions}')
            self.stdout.write(f'   Active sessions: {active_sessions}')
            self.stdout.write(f'   Expired sessions: {expired_sessions}')

            if expired_sessions > 100:
                self.stdout.write(self.style.WARNING(f'   Consider running: python manage.py cleanup_sessions'))
        except Exception as e:
            self.stdout.write(f'   Error getting session stats: {e}')

        self.stdout.write('')

    def _report_log_sizes(self):
        """Report log file sizes"""
        self.stdout.write(self.style.HTTP_INFO('5. Log File Sizes:'))

        log_dir = os.path.join(settings.BASE_DIR, os.getenv('LOG_DIR', 'logs'))
        if os.path.exists(log_dir):
            total_size = 0
            for f in os.listdir(log_dir):
                filepath = os.path.join(log_dir, f)
                if os.path.isfile(filepath):
                    size = os.path.getsize(filepath)
                    total_size += size
                    self.stdout.write(f'   {f}: {size / (1024*1024):.2f} MB')
            self.stdout.write(f'   Total: {total_size / (1024*1024):.2f} MB')
        else:
            self.stdout.write('   Log directory not found')

        self.stdout.write('')

    def _report_performance_middleware(self):
        """Report performance middleware stats"""
        self.stdout.write(self.style.HTTP_INFO('6. Performance Middleware:'))

        try:
            from src.middleware.performance import get_performance_summary, _performance_metrics
            summary = get_performance_summary()
            self.stdout.write(f'   Stored requests: {len(_performance_metrics["requests"])}')
            self.stdout.write(f'   Max stored: {_performance_metrics["max_stored"]}')
            self.stdout.write(f'   Avg response time: {summary["avg_response_time_ms"]:.1f} ms')
            self.stdout.write(f'   Requests last hour: {summary["requests_last_hour"]}')
        except Exception as e:
            self.stdout.write(f'   Error: {e}')

        self.stdout.write('')

    def _report_gc_stats(self):
        """Report garbage collection statistics"""
        self.stdout.write(self.style.HTTP_INFO('7. Garbage Collection:'))

        gc_stats = gc.get_stats()
        for i, stat in enumerate(gc_stats):
            self.stdout.write(f'   Generation {i}:')
            self.stdout.write(f'      Collections: {stat["collections"]}')
            self.stdout.write(f'      Collected: {stat["collected"]}')
            self.stdout.write(f'      Uncollectable: {stat["uncollectable"]}')

        self.stdout.write(f'   GC enabled: {gc.isenabled()}')
        thresholds = gc.get_threshold()
        self.stdout.write(f'   GC thresholds: {thresholds}')

        self.stdout.write('')

    def _report_large_objects(self):
        """Report large objects in memory"""
        self.stdout.write(self.style.HTTP_INFO('8. Object Counts:'))

        # Count objects by type
        type_counts = {}
        for obj in gc.get_objects():
            obj_type = type(obj).__name__
            type_counts[obj_type] = type_counts.get(obj_type, 0) + 1

        # Sort by count and show top 20
        sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:20]
        for obj_type, count in sorted_types:
            self.stdout.write(f'   {obj_type}: {count}')

        self.stdout.write('')

    def _generate_recommendations(self):
        """Generate optimization recommendations based on findings"""
        recommendations = []

        # Check for expired sessions
        try:
            from django.contrib.sessions.models import Session
            from django.utils import timezone
            expired = Session.objects.filter(expire_date__lt=timezone.now()).count()
            if expired > 100:
                recommendations.append(f'Clean up {expired} expired sessions: python manage.py cleanup_sessions')
        except Exception:
            pass

        # Check cache backend
        if 'locmem' in settings.CACHES['default']['BACKEND'].lower():
            recommendations.append('Consider using Redis for caching instead of LocMemCache')

        # Check performance middleware
        try:
            from src.middleware.performance import _performance_metrics
            if len(_performance_metrics['requests']) > 80:
                recommendations.append('Performance middleware storing many requests - consider reducing max_stored')
        except Exception:
            pass

        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                self.stdout.write(f'{i}. {rec}')
        else:
            self.stdout.write('No immediate recommendations.')

        self.stdout.write('')
