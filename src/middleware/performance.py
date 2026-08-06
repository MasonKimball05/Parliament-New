"""
Performance monitoring middleware for tracking request times and server health.

Uses Django's cache backend (Redis on prod) so metrics are shared across all
Gunicorn workers — process-local dicts would always show 0 on multi-worker setups.

Entries are stored as a Python list of tuples via pickle; no JSON layer.
"""
import time
import logging
from datetime import timedelta
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


class _QueryCounter:
    """
    Counts and times queries via `connection.execute_wrapper`.

    ⚠️ v3.18.7 — WHY NOT `len(connection.queries)`, WHICH IS WHAT THIS REPLACED.
    `connection.queries` is populated only when `connection.queries_logged` is
    true, and that is `force_debug_cursor or settings.DEBUG`
    (django/db/backends/base/base.py:170). Production runs DEBUG=False, so the
    deque is never appended to and the before/after delta was **always 0** —
    for every request, forever. Two consequences, and the second is the one
    that cost something:

      * `avg_db_queries` / `avg_db_time_ms` in `get_performance_summary()` were
        permanently 0;
      * the slow-request alarm below logged `(0 queries, 0ms DB time)` on every
        slow page. In a codebase whose last six weeks are almost entirely N+1
        hunts, an alert that fires on a slow page and reports no database work
        points the investigator away from the answer roughly every time.

    The tell that this was an oversight and not a decision: `db_time_ms` was
    already wrapped in `if settings.DEBUG`, one statement below the unguarded
    count. The author knew DEBUG gates the query log, guarded the timing line,
    and missed the counting line directly above it.

    `execute_wrapper` is independent of DEBUG — it is the same mechanism
    `dev_mode.py:78` uses, which is why prod dev mode could find the N+1s in
    v3.18.3 and v3.18.6 that this middleware could not. Cost is one function
    call and two `perf_counter()` reads per query, which is noise beside the
    query itself.

    Single-database only: `execute_wrapper` is per-connection and this wraps
    `default`. Parliament has one database; if that ever changes, this counts
    a subset rather than reporting zero, which is the better failure.
    """

    __slots__ = ('count', 'total_ms')

    def __init__(self):
        self.count = 0
        self.total_ms = 0.0

    def __call__(self, execute, sql, params, many, context):
        start = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            # In `finally` so a failing query is still counted — a request that
            # is slow *because* queries are erroring is exactly when the number
            # matters.
            self.count += 1
            self.total_ms += (time.perf_counter() - start) * 1000


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

        counter = _QueryCounter()
        start_time = time.perf_counter()

        with connection.execute_wrapper(counter):
            response = self.get_response(request)

        duration_ms = (time.perf_counter() - start_time) * 1000

        db_query_count = counter.count
        db_time_ms = counter.total_ms

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
