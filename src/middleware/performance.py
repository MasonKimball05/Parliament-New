"""
Performance monitoring middleware for tracking request times and server health.
"""
import time
import logging
from django.conf import settings
from django.db import connection
from django.utils import timezone
from datetime import timedelta
import threading

logger = logging.getLogger(__name__)

# Thread-safe storage for performance metrics
_metrics_lock = threading.Lock()
_performance_metrics = {
    'requests': [],  # List of (timestamp, duration_ms, path, db_queries, db_time_ms)
    'max_stored': 100,  # Keep last 100 requests (reduced from 1000 to prevent memory growth)
}


def get_performance_metrics():
    """Get a copy of current performance metrics."""
    with _metrics_lock:
        return {
            'requests': list(_performance_metrics['requests']),
        }


def get_performance_summary():
    """Get a summary of performance metrics."""
    with _metrics_lock:
        requests = _performance_metrics['requests']

        if not requests:
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

        # Filter to recent requests
        recent_requests = [r for r in requests if r[0] >= hour_ago]
        very_recent = [r for r in requests if r[0] >= five_min_ago]

        if recent_requests:
            durations = [r[1] for r in recent_requests]
            db_queries = [r[3] for r in recent_requests]
            db_times = [r[4] for r in recent_requests]

            return {
                'total_requests': len(requests),
                'avg_response_time_ms': round(sum(durations) / len(durations), 1),
                'max_response_time_ms': round(max(durations), 1),
                'slow_requests': len([d for d in durations if d > 1000]),  # >1 second
                'avg_db_queries': round(sum(db_queries) / len(db_queries), 1),
                'avg_db_time_ms': round(sum(db_times) / len(db_times), 1),
                'requests_last_hour': len(recent_requests),
                'requests_last_5min': len(very_recent),
            }

        return {
            'total_requests': len(requests),
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
    with _metrics_lock:
        requests = _performance_metrics['requests']
        slow = [r for r in requests if r[1] > threshold_ms]
        slow.sort(key=lambda x: x[1], reverse=True)
        return slow[:limit]


def clear_old_metrics():
    """Clear metrics older than 24 hours."""
    with _metrics_lock:
        cutoff = timezone.now() - timedelta(hours=24)
        _performance_metrics['requests'] = [
            r for r in _performance_metrics['requests']
            if r[0] >= cutoff
        ]


class PerformanceMiddleware:
    """
    Middleware to track request performance metrics.
    Stores timing data that can be viewed in the admin dashboard.
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
            # In debug mode, we can access query times
            try:
                for query in connection.queries[start_queries:end_queries]:
                    db_time_ms += float(query.get('time', 0)) * 1000
            except (ValueError, TypeError):
                pass

        # Store metrics
        with _metrics_lock:
            _performance_metrics['requests'].append((
                timezone.now(),
                duration_ms,
                request.path,
                db_query_count,
                db_time_ms,
            ))

            # Trim old entries
            if len(_performance_metrics['requests']) > _performance_metrics['max_stored']:
                _performance_metrics['requests'] = _performance_metrics['requests'][-_performance_metrics['max_stored']:]

        # Log slow requests
        if duration_ms > 2000:  # Log requests over 2 seconds
            logger.warning(
                f"Slow request: {request.path} took {duration_ms:.0f}ms "
                f"({db_query_count} queries, {db_time_ms:.0f}ms DB time)"
            )

        # Add server timing header (optional, helps with browser dev tools)
        response['Server-Timing'] = f'total;dur={duration_ms:.1f}, db;dur={db_time_ms:.1f}'

        return response
