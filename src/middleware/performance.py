"""
Performance monitoring middleware for tracking request times and server health.

Uses Django's cache backend (Redis on prod) so metrics are shared across all
Gunicorn workers — process-local dicts would always show 0 on multi-worker setups.

Entries are stored as a Python list of tuples via pickle; no JSON layer.
"""
import time
import logging
from datetime import timedelta
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = 'perf_requests'
MAX_STORED = 500
CACHE_TTL = 60 * 60 * 25  # 25 hours


def _append_metric(entry: tuple):
    """
    Append a single (timestamp, duration_ms, path, db_queries, db_time_ms) tuple.
    Stored directly as a Python list so pickle handles serialisation transparently.
    Race conditions under high concurrency may cause a small number of writes to be
    overwritten; that's acceptable for a dashboard metric.
    """
    entries = cache.get(CACHE_KEY)
    if not isinstance(entries, list):
        entries = []

    entries.append(entry)

    if len(entries) > MAX_STORED:
        entries = entries[-MAX_STORED:]

    cache.set(CACHE_KEY, entries, CACHE_TTL)


def _get_entries() -> list:
    """Return stored entries; always returns a list, never raises."""
    entries = cache.get(CACHE_KEY)
    if not isinstance(entries, list):
        return []
    return entries


def get_performance_metrics():
    """Return raw entries (kept for backwards compat)."""
    return {'requests': _get_entries()}


def get_performance_summary():
    """Return aggregated performance stats for the dashboard."""
    entries = _get_entries()

    empty = {
        'total_requests': 0,
        'avg_response_time_ms': 0,
        'max_response_time_ms': 0,
        'slow_requests': 0,
        'avg_db_queries': 0,
        'avg_db_time_ms': 0,
        'requests_last_hour': 0,
        'requests_last_5min': 0,
    }

    if not entries:
        return empty

    now = timezone.now()
    hour_ago = now - timedelta(hours=1)
    five_min_ago = now - timedelta(minutes=5)

    recent = [r for r in entries if r[0] >= hour_ago]
    very_recent = [r for r in entries if r[0] >= five_min_ago]

    if not recent:
        return {**empty, 'total_requests': len(entries)}

    durations = [r[1] for r in recent]
    db_queries = [r[3] for r in recent]
    db_times = [r[4] for r in recent]

    return {
        'total_requests': len(entries),
        'avg_response_time_ms': round(sum(durations) / len(durations), 1),
        'max_response_time_ms': round(max(durations), 1),
        'slow_requests': sum(1 for d in durations if d > 1000),
        'avg_db_queries': round(sum(db_queries) / len(db_queries), 1),
        'avg_db_time_ms': round(sum(db_times) / len(db_times), 1),
        'requests_last_hour': len(recent),
        'requests_last_5min': len(very_recent),
    }


def get_slow_requests(threshold_ms=1000, limit=10):
    """Return the N slowest requests tracked so far."""
    entries = _get_entries()
    slow = sorted((r for r in entries if r[1] > threshold_ms), key=lambda x: x[1], reverse=True)
    return slow[:limit]


def clear_old_metrics():
    """Drop entries older than 24 hours."""
    entries = _get_entries()
    cutoff = timezone.now() - timedelta(hours=24)
    kept = [r for r in entries if r[0] >= cutoff]
    cache.set(CACHE_KEY, kept, CACHE_TTL)


class PerformanceMiddleware:
    """
    Middleware to track request performance metrics.
    Stores timing data in the shared cache so all Gunicorn workers contribute.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/static/') or request.path == '/health/':
            return self.get_response(request)

        start_queries = len(connection.queries)
        start_time = time.perf_counter()

        response = self.get_response(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        end_queries = len(connection.queries)
        db_query_count = end_queries - start_queries
        db_time_ms = 0.0

        if settings.DEBUG:
            try:
                for query in connection.queries[start_queries:end_queries]:
                    db_time_ms += float(query.get('time', 0)) * 1000
            except (ValueError, TypeError):
                pass

        try:
            _append_metric((
                timezone.now(),
                duration_ms,
                request.path,
                db_query_count,
                db_time_ms,
            ))
        except Exception:
            pass  # Never let metric collection crash a request

        if duration_ms > 2000:
            logger.warning(
                f"Slow request: {request.path} took {duration_ms:.0f}ms "
                f"({db_query_count} queries, {db_time_ms:.0f}ms DB time)"
            )

        response['Server-Timing'] = f'total;dur={duration_ms:.1f}, db;dur={db_time_ms:.1f}'
        return response
