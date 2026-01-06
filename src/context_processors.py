"""
Context processors to make data available to all templates
"""
from src.models_feature_flags import FeatureFlag, PageToggle


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
