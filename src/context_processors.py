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


def feature_flags(request):
    """
    Makes feature flags available in all templates.

    Usage in templates:
        {% if feature_flags.announcements %}
            <!-- Show announcements -->
        {% endif %}
    """
    # Get all enabled feature flags
    enabled_features = {}
    for flag in FeatureFlag.objects.filter(is_enabled=True):
        enabled_features[flag.name] = True

    # Get all enabled pages
    enabled_pages = {}
    for toggle in PageToggle.objects.filter(is_enabled=True):
        enabled_pages[toggle.url_name] = True

    return {
        'feature_flags': enabled_features,
        'enabled_pages': enabled_pages,
    }
