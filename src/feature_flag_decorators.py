"""
Decorators and utilities for feature flags and page toggles
"""
from functools import wraps
from django.shortcuts import render
from django.http import HttpResponseForbidden
from src.models_feature_flags import FeatureFlag, PageToggle


def require_feature_flag(feature_name):
    """
    Decorator to require a feature flag to be enabled.
    If disabled, returns a 403 Forbidden page.

    Usage:
        @require_feature_flag('announcements')
        def announcements_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not FeatureFlag.is_feature_enabled(feature_name):
                return render(request, 'feature_disabled.html', {
                    'feature_name': feature_name,
                }, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_page_enabled(url_name):
    """
    Decorator to require a page toggle to be enabled.
    If disabled, shows a custom message page.

    Usage:
        @require_page_enabled('announcements')
        def announcements_view(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not PageToggle.is_page_enabled(url_name):
                try:
                    toggle = PageToggle.objects.get(url_name=url_name)
                    message = toggle.disabled_message
                    page_name = toggle.display_name
                except PageToggle.DoesNotExist:
                    message = 'This page is currently unavailable.'
                    page_name = 'Page'

                return render(request, 'page_disabled.html', {
                    'page_name': page_name,
                    'message': message,
                }, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def check_feature_enabled(feature_name):
    """
    Helper function to check if a feature is enabled.
    Use this in views or templates.

    Usage:
        if check_feature_enabled('announcements'):
            # show announcements
    """
    return FeatureFlag.is_feature_enabled(feature_name)


def check_page_enabled(url_name):
    """
    Helper function to check if a page is enabled.

    Usage:
        if check_page_enabled('announcements'):
            # show link to announcements
    """
    return PageToggle.is_page_enabled(url_name)
