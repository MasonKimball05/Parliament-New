"""
Context processors to make data available to all templates
"""
from src.models_feature_flags import FeatureFlag, PageToggle


def user_preferences(request):
    """
    Ensures user preferences exist and are available in all templates.
    Creates default preferences if they don't exist.

    Cached per-user for 5 minutes to avoid a get_or_create on every request.
    Cache is invalidated in the preferences save view whenever the user updates
    their settings, and on first creation so defaults are picked up immediately.
    """
    if not request.user.is_authenticated:
        return {'user_prefs': None}

    from django.core.cache import cache
    from src.models import UserPreferences

    cache_key = f'user_prefs_{request.user.pk}'
    preferences = cache.get(cache_key)
    if preferences is None:
        preferences, created = UserPreferences.objects.get_or_create(user=request.user)
        cache.set(cache_key, preferences, 300)
    return {'user_prefs': preferences}


def notifications(request):
    """
    Injects unread notification count and unread chat count into all templates.
    Used by the navbar bell icon and Chats link badge.
    Cached for 60 seconds (notifications) and 30 seconds (chat) to reduce queries.
    """
    if request.user.is_authenticated:
        from django.core.cache import cache

        # Bell notification count
        cache_key = f'notif_count_{request.user.pk}'
        unread_count = cache.get(cache_key)
        if unread_count is None:
            from src.models import Notification
            unread_count = Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count()
            cache.set(cache_key, unread_count, 60)

        # Chat unread count — single aggregated query instead of N per-channel COUNTs.
        # For each receipt: count messages in that channel newer than the last-read message.
        # Uses a subquery so the whole thing is one round-trip to the DB.
        chat_cache_key = f'chat_unread_{request.user.pk}'
        unread_chat = cache.get(chat_cache_key)
        if unread_chat is None:
            from django.db.models import OuterRef, Subquery, IntegerField, Value, Count, Sum, Case, When
            from django.db.models.functions import Coalesce
            from src.models import ChatReadReceipt, ChatMessage

            # Subquery: messages in the receipt's channel that are newer than last_read.
            # NOTE: when this filter matches 0 rows, the GROUP BY produces 0 rows, so the
            # subquery returns NULL — not 0. We handle that with Case/When below.
            newer_msgs = ChatMessage.objects.filter(
                channel=OuterRef('channel'),
                is_deleted=False,
                created_at__gt=OuterRef('last_read_message__created_at'),
            ).values('channel').annotate(n=Count('id')).values('n')

            # Fallback when last_read_message is NULL (receipt exists but user has never
            # opened the channel): treat all messages as unread.
            all_msgs = ChatMessage.objects.filter(
                channel=OuterRef('channel'),
                is_deleted=False,
            ).values('channel').annotate(n=Count('id')).values('n')

            receipts = (
                ChatReadReceipt.objects
                .filter(user=request.user, channel__isnull=False)
                .annotate(
                    unread=Case(
                        # last_read_message IS set → NULL from subquery means 0 newer messages
                        When(
                            last_read_message__isnull=False,
                            then=Coalesce(
                                Subquery(newer_msgs[:1], output_field=IntegerField()),
                                Value(0),
                                output_field=IntegerField(),
                            ),
                        ),
                        # last_read_message IS NULL → count all messages as unread
                        default=Coalesce(
                            Subquery(all_msgs[:1], output_field=IntegerField()),
                            Value(0),
                            output_field=IntegerField(),
                        ),
                        output_field=IntegerField(),
                    )
                )
            )
            # Push the final sum into SQL rather than iterating in Python.
            unread_chat = min(receipts.aggregate(total=Sum('unread'))['total'] or 0, 99)
            cache.set(chat_cache_key, unread_chat, 30)

        return {
            'unread_notification_count': unread_count,
            'unread_chat_count': unread_chat,
        }
    return {'unread_notification_count': 0, 'unread_chat_count': 0}


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


class _TrackedToggleDict(dict):
    """
    A dict that reports every lookup a template makes to dev mode.

    WHY THIS EXISTS
    ---------------
    There are two entirely separate ways to ask whether a feature is on:

      * `FeatureFlag.is_feature_enabled('x')` in Python, which is instrumented
        inside that method, and
      * `{% if feature_flags.x %}` in a template, which never calls that method
        at all — it just indexes the dict this context processor builds.

    Dev mode originally only saw the first, which is why some flags appeared in
    the Flags panel and some didn't (07-28-26). This subclass closes that gap by
    recording lookups at the point templates actually make them.

    It also makes the fail-open/fail-closed asymmetry visible for the first
    time: a name with no enabled row raises KeyError here, Django swallows it
    and substitutes '' (falsy), so the feature silently vanishes from the
    template — while the same name in Python would return True. That is exactly
    how the calendar Subscribe button disappeared for weeks (07-25-26).

    Behaviour is unchanged: KeyError is re-raised so Django's normal
    string_if_invalid path still runs.
    """

    _kind = 'feature flag'

    def __getitem__(self, key):
        from src.dev_mode import record_flag
        try:
            value = super().__getitem__(key)
        except KeyError:
            record_flag(
                key, False,
                f'template lookup, no enabled row → "" (fail-CLOSED; '
                f'Python would return True for this name)',
            )
            raise
        record_flag(key, bool(value), 'template lookup, enabled row')
        return value


class _TrackedPageDict(_TrackedToggleDict):
    _kind = 'page toggle'

    def __getitem__(self, key):
        from src.dev_mode import record_flag
        try:
            value = super(_TrackedToggleDict, self).__getitem__(key)
        except KeyError:
            record_flag(key, False, 'page toggle template lookup, no enabled row → ""')
            raise
        record_flag(key, bool(value), 'page toggle template lookup, enabled row')
        return value


def feature_flags(request):
    """
    Makes feature flags available in all templates.
    Cached for 60 seconds to reduce database queries on every request.

    Usage in templates:
        {% if feature_flags.announcements %}
            <!-- Show announcements -->
        {% endif %}

    The two dicts are tracked subclasses so dev mode can report which flags a
    template actually consulted — see _TrackedToggleDict. They pickle fine, so
    tracking survives the cache round-trip below.
    """
    from django.core.cache import cache

    # Try to get from cache first
    cache_key = 'context_feature_flags'
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    # Get all enabled feature flags
    enabled_features = _TrackedToggleDict()
    for flag in FeatureFlag.objects.filter(is_enabled=True):
        enabled_features[flag.name] = True

    # Get all enabled pages
    enabled_pages = _TrackedPageDict()
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

        # Reuse the feature_flags cache (populated by the feature_flags context processor)
        # to avoid a separate DB SELECT on every request. Falls back to a direct lookup
        # only when that cache is cold (first request after server restart or 60s idle).
        _ff_cached = cache.get('context_feature_flags')
        if _ff_cached is not None:
            is_maintenance = bool(_ff_cached.get('feature_flags', {}).get('maintenance_mode', False))
        else:
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


def two_factor_status(request):
    """
    Injects backup-code warning state into all templates so a banner can
    be shown site-wide when the user's 2FA backup codes need attention.

    Warning is shown when:
    - 2FA is enabled AND codes have never been acknowledged (not viewed after generation)
    - 2FA is enabled AND no backup device exists
    - 2FA is enabled AND ≤ 2 codes remain

    Cached per-user for 5 minutes — this used to fire 2 uncached DB queries on
    every single page load. The cache is invalidated in preferences when the user
    acknowledges backup codes or regenerates them.
    """
    if not request.user.is_authenticated:
        return {'backup_codes_warning': False, 'backup_codes_remaining': None}

    from django.core.cache import cache

    cache_key = f'2fa_status_{request.user.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from django_otp import user_has_device
    from django_otp.plugins.otp_static.models import StaticDevice

    has_2fa = user_has_device(request.user)
    if not has_2fa:
        result = {'backup_codes_warning': False, 'backup_codes_remaining': None}
        cache.set(cache_key, result, 300)
        return result

    backup_device = StaticDevice.objects.filter(
        user=request.user, name='backup', confirmed=True
    ).first()
    remaining = backup_device.token_set.count() if backup_device else 0

    warning = (
        not backup_device
        or remaining == 0
        or not request.user.backup_codes_acknowledged
        or remaining <= 2
    )
    result = {'backup_codes_warning': warning, 'backup_codes_remaining': remaining}
    cache.set(cache_key, result, 300)
    return result


def _get_client_ip(request):
    """Helper to get client IP address"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')
