"""
GeoRestrictionMiddleware

Reads the geo suspicion flag set at login and:
  1. Sets request.geo_suspicious (bool) for views/templates to read
  2. Blocks access to sensitive data-export endpoints for non-US sessions
  3. Logs all authenticated requests from flagged sessions to the security log

A session is flagged when the user's login IP resolves to a country other
than the United States. Members who travel abroad will be flagged for that
session only; logging in again from the US clears the flag.
"""
import logging
from django.http import HttpResponseForbidden
from django.shortcuts import render

logger = logging.getLogger('admin_actions')

# Paths that are blocked for non-US sessions.
# These are bulk data operations that should not be accessible while abroad.
RESTRICTED_PATH_PREFIXES = (
    '/directory/export/',
    '/officers/activity-logs/export/',
    '/user_list/export/',
    '/kai/reports/export/',
    '/service-hours/submissions/export/',
    '/officers/db-dump/',
    '/admin-v2/security/honeypot-logs/clear/',  # Bulk delete — too destructive from abroad
)


class GeoRestrictionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Set attribute so all views and templates can check it
        request.geo_suspicious = False

        if request.user.is_authenticated:
            request.geo_suspicious = request.session.get('login_geo_suspicious', False)

            if request.geo_suspicious:
                path = request.path_info

                # Block restricted endpoints
                if any(path.startswith(prefix) for prefix in RESTRICTED_PATH_PREFIXES):
                    geo = request.session.get('login_geo', {})
                    country = request.session.get('login_geo_country', 'Unknown')
                    city = request.session.get('login_geo_city', '')
                    location = f"{city}, {country}" if city else country

                    logger.warning(
                        f"GEO_BLOCK: User '{request.user.user_id}' attempted to access "
                        f"restricted export '{path}' from non-US location ({location}). Blocked."
                    )
                    return render(request, 'geo_restricted.html', {
                        'location': location,
                        'restricted_path': path,
                    }, status=403)

                # Log all authenticated requests from flagged sessions (non-GET only, to reduce noise)
                if request.method not in ('GET', 'HEAD'):
                    logger.warning(
                        f"GEO_SUSPICIOUS: User '{request.user.user_id}' ({request.method}) "
                        f"{path} — session flagged as non-US login"
                    )

        return self.get_response(request)
