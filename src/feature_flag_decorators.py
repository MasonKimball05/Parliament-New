"""
Decorators and utilities for feature flags and page toggles
"""
from functools import wraps
from django.shortcuts import render
from django.http import HttpResponseForbidden
from src.models_feature_flags import FeatureFlag, PageToggle


def require_feature_flag(*feature_names):
    """
    Decorator to require one or more feature flags to be enabled.
    If any is disabled, returns a 403 Forbidden page.

    Usage:
        @require_feature_flag('announcements')
        def announcements_view(request):
            ...

        @require_feature_flag('attendance_tracking', 'event_attendance')
        def event_attendance_list(request):
            ...

    ⚠️ v3.26.0 — TAKES *MULTIPLE NAMES IN ONE CALL, NOT STACKED DECORATORS.
    `FeatureFlag.is_feature_enabled` is cached per-name, so two STACKED
    `@require_feature_flag(...)` decorators on the same view each pay their
    own cache lookup — on a cold cache that is two separate
    `src_featureflag` queries where one would do, and `test_url_smoke` /
    `test_detail_route_smoke` / `test_query_budgets` all fail a page whose
    query count grew this way (v3.19.7 fixed the same shape once already, for
    a loop rather than a stack — see `FeatureFlag.resolve_many`). Passing
    every name to a single decorator call resolves them in one query via
    `resolve_many` instead.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            results = FeatureFlag.resolve_many(feature_names)
            disabled = [name for name in feature_names if not results[name]]
            if disabled:
                return render(request, 'feature_disabled.html', {
                    'feature_name': disabled[0],
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
            # Recorded for the dev-mode Flags panel: @require_page_enabled is a
            # gate like any other, and a page 403ing because of a PageToggle row
            # is otherwise indistinguishable from a permission failure.
            from src.dev_mode import record_flag

            page_enabled = PageToggle.is_page_enabled(url_name)
            record_flag(url_name, page_enabled, 'page toggle (@require_page_enabled)')
            if not page_enabled:
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
