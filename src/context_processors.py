"""
Context processors to make data available to all templates
"""
from src.models_feature_flags import FeatureFlag, PageToggle


def user_preferences(request):
    """
    Ensures user preferences exist and are available in all templates.
    Creates default preferences if they don't exist.

    This fixes the issue where the header shows nothing by default
    for users who haven't visited the preferences page yet.
    """
    if request.user.is_authenticated:
        from src.models import UserPreferences
        # Get or create preferences - this ensures defaults are applied
        preferences, created = UserPreferences.objects.get_or_create(user=request.user)
        return {'user_prefs': preferences}
    return {'user_prefs': None}


def notifications(request):
    """
    Injects unread notification count into all templates.
    Used by the navbar bell icon to show the badge count.
    Cached for 60 seconds to reduce database queries.
    """
    if request.user.is_authenticated:
        from django.core.cache import cache
        cache_key = f'notif_count_{request.user.pk}'
        unread_count = cache.get(cache_key)
        if unread_count is None:
            from src.models import Notification
            unread_count = Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count()
            cache.set(cache_key, unread_count, 60)  # Cache for 60 seconds
        return {'unread_notification_count': unread_count}
    return {'unread_notification_count': 0}


def impersonation(request):
    """
    Exposes impersonation state to all templates.
    When an admin is logged in as another user, is_impersonating is True
    and impersonation_original_name is the admin's display name.
    """
    if request.user.is_authenticated:
        original_id = request.session.get('_impersonating_original_user_id')
        if original_id:
            return {
                'is_impersonating': True,
                'impersonation_original_name': request.session.get(
                    '_impersonating_original_user_name', 'Admin'
                ),
            }
    return {'is_impersonating': False, 'impersonation_original_name': None}


def feature_flags(request):
    """
    Makes feature flags available in all templates.
    Cached for 60 seconds to reduce database queries on every request.

    Usage in templates:
        {% if feature_flags.announcements %}
            <!-- Show announcements -->
        {% endif %}
    """
    from django.core.cache import cache

    # Try to get from cache first
    cache_key = 'context_feature_flags'
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    # Get all enabled feature flags
    enabled_features = {}
    for flag in FeatureFlag.objects.filter(is_enabled=True):
        enabled_features[flag.name] = True

    # Get all enabled pages
    enabled_pages = {}
    for toggle in PageToggle.objects.filter(is_enabled=True):
        enabled_pages[toggle.url_name] = True

    result = {
        'feature_flags': enabled_features,
        'enabled_pages': enabled_pages,
    }

    # Cache for 60 seconds
    cache.set(cache_key, result, 60)
    return result


def maintenance_mode(request):
    """
    Provides maintenance mode information for admin users.
    Shows a banner with stats when maintenance mode is active.
    Also provides upcoming scheduled maintenance info for warning banners.
    """
    context = {
        'maintenance_mode_active': False,
        'maintenance_info': None,
        'admin_debug_info': None,
        'upcoming_maintenance': None,
    }

    # Check if maintenance mode is enabled
    try:
        from django.core.cache import cache

        is_maintenance = FeatureFlag.is_feature_enabled('maintenance_mode')
        context['maintenance_mode_active'] = is_maintenance

        if not is_maintenance:
            # Clear maintenance stats when mode is disabled
            cache.delete('maintenance_mode_started_at')
            cache.delete('maintenance_blocked_count')
            return context

        # Only provide detailed info to admins
        if request.user.is_authenticated and getattr(request.user, 'is_admin', False):
            from django.utils import timezone
            from django.conf import settings
            import sys
            import os

            # Get maintenance start time from cache or set it
            cache_key = 'maintenance_mode_started_at'
            started_at = cache.get(cache_key)
            if not started_at:
                started_at = timezone.now()
                cache.set(cache_key, started_at, 86400)  # Cache for 24 hours

            # Get blocked request count
            blocked_count_key = 'maintenance_blocked_count'
            blocked_count = cache.get(blocked_count_key, 0)

            # Calculate duration
            duration = timezone.now() - started_at
            hours, remainder = divmod(duration.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)

            if hours > 0:
                duration_str = f"{int(hours)}h {int(minutes)}m"
            elif minutes > 0:
                duration_str = f"{int(minutes)}m {int(seconds)}s"
            else:
                duration_str = f"{int(seconds)}s"

            context['maintenance_info'] = {
                'started_at': started_at,
                'duration': duration_str,
                'blocked_requests': blocked_count,
            }

            # Add debug info for admins - technical behind-the-scenes data
            context['admin_debug_info'] = {
                # Request info
                'request': {
                    'path': request.path,
                    'method': request.method,
                    'content_type': request.content_type,
                    'is_secure': request.is_secure(),
                    'is_ajax': request.headers.get('X-Requested-With') == 'XMLHttpRequest',
                    'user_agent': request.META.get('HTTP_USER_AGENT', 'Unknown')[:100],
                    'client_ip': _get_client_ip(request),
                    'referer': request.META.get('HTTP_REFERER', 'None')[:100] if request.META.get('HTTP_REFERER') else 'None',
                },
                # Server info
                'server': {
                    'python_version': sys.version.split()[0],
                    'django_debug': settings.DEBUG,
                    'server_time': timezone.localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S %Z'),
                    'timezone': str(settings.TIME_ZONE),
                    'allowed_hosts': ', '.join(settings.ALLOWED_HOSTS[:3]) + ('...' if len(settings.ALLOWED_HOSTS) > 3 else ''),
                },
                # Database info
                'database': {
                    'engine': settings.DATABASES['default']['ENGINE'].split('.')[-1],
                    'name': settings.DATABASES['default'].get('NAME', 'N/A'),
                },
                # Cache info
                'cache': {
                    'backend': settings.CACHES['default']['BACKEND'].split('.')[-1],
                },
                # Session info
                'session': {
                    'session_key': request.session.session_key[:8] + '...' if request.session.session_key else 'None',
                    'is_empty': request.session.is_empty(),
                },
            }
    except Exception:
        pass

    # Get upcoming scheduled maintenance (for warning banner)
    try:
        from src.models_feature_flags import ScheduledMaintenance
        upcoming = ScheduledMaintenance.get_upcoming_maintenance()
        if upcoming and not upcoming.maintenance_started:
            context['upcoming_maintenance'] = {
                'title': upcoming.title,
                'message': upcoming.message,
                'scheduled_start': upcoming.scheduled_start,
                'time_until': upcoming.time_until_start,
                'estimated_duration': upcoming.estimated_duration_minutes,
                'estimated_end': upcoming.estimated_end_time,
            }
    except Exception:
        pass

    return context


def _get_client_ip(request):
    """Helper to get client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')
