"""
Debug Panel API Endpoints for Admin Users
Provides F12-like developer tools built into the site during maintenance mode.
"""

import json
import sys
import os
from functools import wraps
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from django.db import connection
from django.template import engines


def admin_required(view_func):
    """Decorator to require admin access for debug endpoints"""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        if not getattr(request.user, 'is_admin', False):
            return JsonResponse({'error': 'Admin access required'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


@require_http_methods(["GET"])
@admin_required
def debug_request_info(request):
    """Get detailed request information"""
    # Get all headers
    headers = {}
    for key, value in request.META.items():
        if key.startswith('HTTP_'):
            header_name = key[5:].replace('_', '-').title()
            headers[header_name] = value
        elif key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            headers[key.replace('_', '-').title()] = value

    # Get cookies (names only for security)
    cookies = {name: f'{value[:20]}...' if len(str(value)) > 20 else value
               for name, value in request.COOKIES.items()}

    # Get query params
    query_params = dict(request.GET.items())

    return JsonResponse({
        'method': request.method,
        'path': request.path,
        'full_path': request.get_full_path(),
        'is_secure': request.is_secure(),
        'is_ajax': request.headers.get('X-Requested-With') == 'XMLHttpRequest',
        'content_type': request.content_type,
        'encoding': request.encoding,
        'headers': headers,
        'cookies': cookies,
        'query_params': query_params,
        'scheme': request.scheme,
        'host': request.get_host(),
    })


@require_http_methods(["GET"])
@admin_required
def debug_server_info(request):
    """Get server and environment information"""
    import django
    import platform

    # Get memory usage if psutil available
    memory_info = None
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_info = {
            'rss_mb': round(process.memory_info().rss / 1024 / 1024, 2),
            'vms_mb': round(process.memory_info().vms / 1024 / 1024, 2),
            'percent': round(process.memory_percent(), 2),
        }
    except ImportError:
        pass

    # Get CPU info if available
    cpu_info = None
    try:
        import psutil
        cpu_info = {
            'percent': psutil.cpu_percent(interval=0.1),
            'count': psutil.cpu_count(),
        }
    except ImportError:
        pass

    return JsonResponse({
        'python_version': sys.version,
        'django_version': django.__version__,
        'platform': platform.platform(),
        'debug_mode': settings.DEBUG,
        'server_time': timezone.now().isoformat(),
        'timezone': str(settings.TIME_ZONE),
        'allowed_hosts': settings.ALLOWED_HOSTS[:5],
        'installed_apps': [app.split('.')[-1] for app in settings.INSTALLED_APPS],
        'middleware': [m.split('.')[-1] for m in settings.MIDDLEWARE],
        'memory': memory_info,
        'cpu': cpu_info,
        'pid': os.getpid(),
    })


@require_http_methods(["GET"])
@admin_required
def debug_database_info(request):
    """Get database connection and recent query information"""
    db_settings = settings.DATABASES['default']

    # Get query log from connection (if debug mode has captured them)
    queries = []
    if settings.DEBUG:
        for query in connection.queries[-20:]:  # Last 20 queries
            queries.append({
                'sql': query['sql'][:200] + '...' if len(query['sql']) > 200 else query['sql'],
                'time': query['time'],
            })

    # Get database stats
    stats = {}
    try:
        with connection.cursor() as cursor:
            # PostgreSQL stats
            if 'postgresql' in db_settings['ENGINE']:
                cursor.execute("""
                    SELECT
                        pg_database_size(current_database()) as db_size,
                        (SELECT count(*) FROM pg_stat_activity WHERE state = 'active') as active_connections
                """)
                row = cursor.fetchone()
                if row:
                    stats['db_size_mb'] = round(row[0] / 1024 / 1024, 2)
                    stats['active_connections'] = row[1]
    except Exception as e:
        stats['error'] = str(e)

    return JsonResponse({
        'engine': db_settings['ENGINE'].split('.')[-1],
        'name': db_settings.get('NAME', 'N/A'),
        'host': db_settings.get('HOST', 'localhost'),
        'port': db_settings.get('PORT', 'default'),
        'conn_max_age': db_settings.get('CONN_MAX_AGE', 0),
        'recent_queries': queries,
        'query_count': len(connection.queries) if settings.DEBUG else 'N/A (DEBUG=False)',
        'stats': stats,
    })


@require_http_methods(["GET"])
@admin_required
def debug_cache_info(request):
    """Get cache statistics and keys"""
    cache_backend = settings.CACHES['default']['BACKEND']
    cache_info = {
        'backend': cache_backend.split('.')[-1],
        'location': settings.CACHES['default'].get('LOCATION', 'N/A'),
    }

    # Try to get cache stats for Redis
    if 'redis' in cache_backend.lower():
        try:
            from django_redis import get_redis_connection
            conn = get_redis_connection("default")
            info = conn.info()
            cache_info['stats'] = {
                'used_memory_mb': round(info.get('used_memory', 0) / 1024 / 1024, 2),
                'connected_clients': info.get('connected_clients', 0),
                'total_keys': info.get('db0', {}).get('keys', 0) if isinstance(info.get('db0'), dict) else conn.dbsize(),
                'hits': info.get('keyspace_hits', 0),
                'misses': info.get('keyspace_misses', 0),
            }
            # Get sample keys
            keys = conn.keys('parliament:*')[:20]
            cache_info['sample_keys'] = [k.decode() if isinstance(k, bytes) else k for k in keys]
        except Exception as e:
            cache_info['error'] = str(e)

    # Try some common cache keys
    common_keys = [
        'maintenance_mode_started_at',
        'maintenance_blocked_count',
        'feature_flag_cache',
    ]
    cache_info['known_values'] = {}
    for key in common_keys:
        value = cache.get(key)
        if value is not None:
            cache_info['known_values'][key] = str(value)[:100]

    return JsonResponse(cache_info)


@require_http_methods(["POST"])
@admin_required
def debug_cache_clear(request):
    """Clear a specific cache key or all cache"""
    try:
        data = json.loads(request.body)
        key = data.get('key')

        if key == '__all__':
            cache.clear()
            return JsonResponse({'success': True, 'message': 'All cache cleared'})
        elif key:
            cache.delete(key)
            return JsonResponse({'success': True, 'message': f'Cache key "{key}" cleared'})
        else:
            return JsonResponse({'error': 'No key specified'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
@admin_required
def debug_session_info(request):
    """Get session data"""
    session_data = {}
    for key in request.session.keys():
        value = request.session[key]
        # Serialize safely
        try:
            json.dumps(value)
            session_data[key] = value
        except (TypeError, ValueError):
            session_data[key] = str(value)[:100]

    return JsonResponse({
        'session_key': request.session.session_key,
        'is_empty': request.session.is_empty(),
        'expiry_age': request.session.get_expiry_age(),
        'expiry_date': request.session.get_expiry_date().isoformat() if request.session.get_expiry_date() else None,
        'data': session_data,
    })


@require_http_methods(["POST"])
@admin_required
def debug_session_edit(request):
    """Edit or delete session data"""
    try:
        data = json.loads(request.body)
        action = data.get('action')
        key = data.get('key')
        value = data.get('value')

        if action == 'set' and key:
            request.session[key] = value
            return JsonResponse({'success': True, 'message': f'Session key "{key}" set'})
        elif action == 'delete' and key:
            if key in request.session:
                del request.session[key]
            return JsonResponse({'success': True, 'message': f'Session key "{key}" deleted'})
        elif action == 'clear':
            request.session.flush()
            return JsonResponse({'success': True, 'message': 'Session cleared'})
        else:
            return JsonResponse({'error': 'Invalid action'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
@admin_required
def debug_feature_flags(request):
    """Get all feature flags and their status"""
    from src.models_feature_flags import FeatureFlag, PageToggle

    flags = []
    for flag in FeatureFlag.objects.all().order_by('name'):
        flags.append({
            'id': flag.id,
            'name': flag.name,
            'description': flag.description,
            'is_enabled': flag.is_enabled,
            'updated_at': flag.updated_at.isoformat() if hasattr(flag, 'updated_at') else None,
        })

    pages = []
    for toggle in PageToggle.objects.all().order_by('url_name'):
        pages.append({
            'id': toggle.id,
            'url_name': toggle.url_name,
            'display_name': toggle.display_name,
            'is_enabled': toggle.is_enabled,
        })

    return JsonResponse({
        'feature_flags': flags,
        'page_toggles': pages,
    })


@require_http_methods(["POST"])
@admin_required
def debug_toggle_flag(request):
    """Toggle a feature flag"""
    try:
        data = json.loads(request.body)
        flag_id = data.get('id')
        flag_type = data.get('type', 'feature')

        if flag_type == 'feature':
            from src.models_feature_flags import FeatureFlag
            flag = FeatureFlag.objects.get(id=flag_id)
            flag.is_enabled = not flag.is_enabled
            flag.save()
            return JsonResponse({
                'success': True,
                'name': flag.name,
                'is_enabled': flag.is_enabled,
            })
        elif flag_type == 'page':
            from src.models_feature_flags import PageToggle
            toggle = PageToggle.objects.get(id=flag_id)
            toggle.is_enabled = not toggle.is_enabled
            toggle.save()
            return JsonResponse({
                'success': True,
                'name': toggle.url_name,
                'is_enabled': toggle.is_enabled,
            })
        else:
            return JsonResponse({'error': 'Invalid flag type'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
@admin_required
def debug_error_logs(request):
    """Get recent error logs"""
    log_file = os.path.join(settings.BASE_DIR, 'logs', 'django_actions.log')
    errors = []

    try:
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                # Read last 100 lines
                lines = f.readlines()[-100:]
                for line in lines:
                    if 'ERROR' in line or 'WARNING' in line or 'CRITICAL' in line:
                        errors.append(line.strip())
    except Exception as e:
        errors.append(f'Error reading log file: {e}')

    return JsonResponse({
        'log_file': log_file,
        'recent_errors': errors[-30:],  # Last 30 errors
        'total_errors': len(errors),
    })


@require_http_methods(["POST"])
@admin_required
def debug_clear_logs(request):
    """Clear the error log file"""
    log_file = os.path.join(settings.BASE_DIR, 'logs', 'django_actions.log')

    try:
        if os.path.exists(log_file):
            with open(log_file, 'w') as f:
                f.write('')  # Clear the file
            return JsonResponse({'success': True, 'message': 'Log file cleared'})
        else:
            return JsonResponse({'error': 'Log file not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
@admin_required
def debug_template_context(request):
    """Get available template context processors and their keys"""
    context_processors = []
    for processor in settings.TEMPLATES[0]['OPTIONS'].get('context_processors', []):
        context_processors.append(processor.split('.')[-1])

    # Get current user's permissions and roles
    user_info = {}
    if request.user.is_authenticated:
        user_info = {
            'user_id': getattr(request.user, 'user_id', str(request.user.pk)),
            'name': request.user.get_display_name() if hasattr(request.user, 'get_display_name') else str(request.user),
            'is_admin': getattr(request.user, 'is_admin', False),
            'is_officer': getattr(request.user, 'is_officer', False),
            'is_superuser': getattr(request.user, 'is_superuser', False),
            'is_staff': getattr(request.user, 'is_staff', False),
            'member_type': getattr(request.user, 'member_type', 'N/A'),
            'member_status': getattr(request.user, 'member_status', 'N/A'),
        }
        # Get roles
        if hasattr(request.user, 'roles'):
            try:
                user_info['roles'] = [role.name for role in request.user.roles.all()[:10]]
            except Exception:
                user_info['roles'] = []

    return JsonResponse({
        'context_processors': context_processors,
        'user_info': user_info,
        'available_context_keys': [
            'user', 'perms', 'csrf_token', 'request', 'messages',
            'feature_flags', 'enabled_pages', 'user_prefs',
            'unread_notification_count', 'maintenance_mode_active',
            'maintenance_info', 'admin_debug_info', 'upcoming_maintenance',
        ],
    })


@require_http_methods(["GET"])
@admin_required
def debug_performance_metrics(request):
    """
    Get performance metrics from PerformanceMiddleware.

    ⚠️ v3.18.7 — THIS ENDPOINT RETURNED ZEROS FOR SIX MONTHS. Worth recording,
    because it broke in a way no error ever surfaced:

      * it read the cache key `perf_metrics_recent`, and the middleware writes
        `perf_requests`. `git log -S` dates the read to 2026-02-08 and it was
        never written by anything, at any point — so every field below derived
        from it was 0 on every call this endpoint has ever served;
      * the shapes did not match either. This read `m.get('response_time')` on
        each entry; the middleware stores plain TUPLES. Even with the key
        corrected, this would have raised AttributeError. Two independent
        breaks stacked, the outer one masking the inner.

    It now delegates to `get_performance_summary()` — the same function the
    admin-v2 dashboard uses, i.e. the one reader that was known to work — so
    there is a single source of truth for these numbers and no second copy of
    the aggregation logic to drift.

    (The 08-06 review suggested deleting this endpoint outright on the grounds
    that the dashboard already covers it. Deviated deliberately: this is one of
    six sibling `/api/debug/*` endpoints, deleting one member of that family
    leaves a hole for the next person to wonder about, and the rewrite is ten
    lines against a data source that already exists.)
    """
    from src.middleware.performance import get_performance_summary, get_slow_requests

    summary = get_performance_summary()
    slow = get_slow_requests(threshold_ms=1000, limit=10)

    # Get blocked request count from maintenance mode
    blocked_count = cache.get('maintenance_blocked_count', 0)
    started_at = cache.get('maintenance_mode_started_at')

    return JsonResponse({
        'maintenance_blocked_requests': blocked_count,
        'maintenance_started_at': started_at.isoformat() if started_at else None,
        'recent_metrics_count': summary['total_requests'],
        # v3.19.3: the averages below are over a 1-in-N sample (slow requests
        # kept unconditionally), so the response says so rather than leaving a
        # caller to assume otherwise. `total_requests` is exact.
        #
        # v3.19.4: `requests_last_hour` renamed to `samples_last_hour` — it was
        # never a request count, and the old name invited exactly the reading it
        # could not support. `sampled_requests` added: the exact count of what
        # was stored, which is the honest denominator for the averages below.
        # This endpoint is admin-only debug JSON with no in-repo consumer
        # (grepped), so the rename costs nothing.
        'sampled': summary.get('sampled', False),
        'sample_rate': summary.get('sample_rate'),
        'sampled_requests': summary.get('sampled_requests'),
        'stored_samples': summary.get('stored_samples'),
        'samples_last_hour': summary['samples_last_hour'],
        'avg_response_time_ms': summary['avg_response_time_ms'],
        'max_response_time_ms': summary['max_response_time_ms'],
        'avg_db_queries': summary['avg_db_queries'],
        'avg_db_time_ms': summary['avg_db_time_ms'],
        # Entries are (timestamp, duration_ms, path, db_queries, db_time_ms)
        # tuples — serialised field by field because a datetime is not JSON.
        'slowest_requests': [
            {
                'at': entry[0].isoformat(),
                'duration_ms': round(entry[1], 1),
                'path': entry[2],
                'db_queries': entry[3],
                'db_time_ms': round(entry[4], 1),
            }
            for entry in slow
        ],
    })


@require_http_methods(["GET"])
@admin_required
def debug_users_online(request):
    """Get currently online/active users"""
    from src.models import ParliamentUser

    # Get users who have been active in the last 15 minutes
    # We can check session data or use a simpler approach
    online_users = []

    # Check for active sessions (this is a simplified approach)
    try:
        from django.contrib.sessions.models import Session
        from django.utils import timezone as tz
        import datetime

        active_sessions = Session.objects.filter(
            expire_date__gt=tz.now()
        ).order_by('-expire_date')[:50]

        for session in active_sessions:
            data = session.get_decoded()
            user_id = data.get('_auth_user_id')
            if user_id:
                try:
                    user = ParliamentUser.objects.get(pk=user_id)
                    online_users.append({
                        'user_id': user.user_id if hasattr(user, 'user_id') else str(user.pk),
                        'name': user.get_display_name() if hasattr(user, 'get_display_name') else str(user),
                        'is_admin': getattr(user, 'is_admin', False),
                        'session_expires': session.expire_date.isoformat(),
                    })
                except ParliamentUser.DoesNotExist:
                    pass
    except Exception as e:
        return JsonResponse({'error': str(e), 'online_users': []})

    return JsonResponse({
        'online_users': online_users[:20],  # Limit to 20
        'total_active_sessions': len(online_users),
    })
