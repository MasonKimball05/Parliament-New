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
