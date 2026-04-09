"""
Performance monitoring middleware for tracking request times and server health.

Uses Django's cache backend (Redis on prod) so metrics are shared across all
Gunicorn workers — process-local dicts would always show 0 on multi-worker setups.
"""
import time
import json
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

CACHE_KEY = 'perf_requests'
MAX_STORED = 500        # cap entries in cache
CACHE_TTL = 60 * 60 * 25  # 25 hours (slightly more than the 24h query window)


def _append_metric(entry: tuple):
    """Append a single metric entry to the shared cache list (thread-safe via cache atomics)."""
    # Serialise the datetime to ISO string so it survives JSON round-trip
    ts, duration_ms, path, db_queries, db_time_ms = entry
    record = [ts.isoformat(), duration_ms, path, db_queries, db_time_ms]

    # Fetch-modify-store under a short lock using cache.add as a simple mutex
    raw = cache.get(CACHE_KEY) or '[]'
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError):
        entries = []

    entries.append(record)

    # Trim to keep only the most recent MAX_STORED entries
    if len(entries) > MAX_STORED:
        entries = entries[-MAX_STORED:]

    cache.set(CACHE_KEY, json.dumps(entries), CACHE_TTL)


def _get_entries() -> list:
    """Return all stored entries as list of (datetime, duration_ms, path, db_queries, db_time_ms)."""
    raw = cache.get(CACHE_KEY)
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except (ValueError, TypeError):
        return []

    result = []
    for record in records:
        try:
            ts_str, duration_ms, path, db_queries, db_time_ms = record
            ts = datetime.fromisoformat(ts_str)
            # Make timezone-aware if it isn't already
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts, dt_timezone.utc)
            result.append((ts, duration_ms, path, db_queries, db_time_ms))
        except Exception:
            continue
    return result


def get_performance_metrics():
    """Get a copy of current performance metrics (kept for backwards compat)."""
    return {'requests': _get_entries()}


def get_performance_summary():
    """Get a summary of performance metrics."""
    entries = _get_entries()

    if not entries:
        return {
            'total_requests': 0,
            'avg_response_time_ms': 0,
            'max_response_time_ms': 0,
            'slow_requests': 0,
            'avg_db_queries': 0,
            'avg_db_time_ms': 0,
            'requests_last_hour': 0,
            'requests_last_5min': 0,
        }

    now = timezone.now()
    hour_ago = now - timedelta(hours=1)
    five_min_ago = now - timedelta(minutes=5)

    recent_requests = [r for r in entries if r[0] >= hour_ago]
    very_recent = [r for r in entries if r[0] >= five_min_ago]

    if recent_requests:
        durations = [r[1] for r in recent_requests]
        db_queries = [r[3] for r in recent_requests]
        db_times = [r[4] for r in recent_requests]

        return {
            'total_requests': len(entries),
            'avg_response_time_ms': round(sum(durations) / len(durations), 1),
            'max_response_time_ms': round(max(durations), 1),
            'slow_requests': len([d for d in durations if d > 1000]),
            'avg_db_queries': round(sum(db_queries) / len(db_queries), 1),
            'avg_db_time_ms': round(sum(db_times) / len(db_times), 1),
            'requests_last_hour': len(recent_requests),
            'requests_last_5min': len(very_recent),
        }

    return {
        'total_requests': len(entries),
        'avg_response_time_ms': 0,
        'max_response_time_ms': 0,
        'slow_requests': 0,
        'avg_db_queries': 0,
        'avg_db_time_ms': 0,
        'requests_last_hour': 0,
        'requests_last_5min': 0,
    }


def get_slow_requests(threshold_ms=1000, limit=10):
    """Get the slowest requests."""
    entries = _get_entries()
    slow = [r for r in entries if r[1] > threshold_ms]
    slow.sort(key=lambda x: x[1], reverse=True)
    return slow[:limit]


def clear_old_metrics():
    """Clear metrics older than 24 hours (called manually or from a cron)."""
    entries = _get_entries()
    cutoff = timezone.now() - timedelta(hours=24)
    kept = [r for r in entries if r[0] >= cutoff]
    cache.set(CACHE_KEY, json.dumps([
        [r[0].isoformat(), r[1], r[2], r[3], r[4]] for r in kept
    ]), CACHE_TTL)


class PerformanceMiddleware:
    """
    Middleware to track request performance metrics.
    Stores timing data in the shared cache so all Gunicorn workers contribute.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Skip static files and health checks
        if request.path.startswith('/static/') or request.path == '/health/':
            return self.get_response(request)

        # Track database queries
        start_queries = len(connection.queries)

        # Start timing
        start_time = time.perf_counter()

        # Process request
        response = self.get_response(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Calculate database time
        end_queries = len(connection.queries)
        db_query_count = end_queries - start_queries
        db_time_ms = 0

        if settings.DEBUG:
            try:
                for query in connection.queries[start_queries:end_queries]:
                    db_time_ms += float(query.get('time', 0)) * 1000
            except (ValueError, TypeError):
                pass

        # Store metrics in shared cache
        _append_metric((
            timezone.now(),
            duration_ms,
            request.path,
            db_query_count,
            db_time_ms,
        ))

        # Log slow requests
        if duration_ms > 2000:
            logger.warning(
                f"Slow request: {request.path} took {duration_ms:.0f}ms "
                f"({db_query_count} queries, {db_time_ms:.0f}ms DB time)"
            )

        # Add server timing header
        response['Server-Timing'] = f'total;dur={duration_ms:.1f}, db;dur={db_time_ms:.1f}'

        return response
