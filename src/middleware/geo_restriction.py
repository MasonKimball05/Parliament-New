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
from django.urls import Resolver404, resolve

logger = logging.getLogger('admin_actions')

#: Bulk data operations that should not be reachable while abroad, named by
#: their **URL name** rather than their path.
#:
#: v3.17.5 — WHY NAMES AND NOT PATHS
#: ---------------------------------
#: This was a tuple of path prefixes matched with `path.startswith(...)`. Every
#: entry in it happened to have a *static* prefix, which hid a structural gap:
#: a route with a parameter in the middle of it cannot be expressed as a prefix
#: at all.
#:
#: `event_signup_export` is exactly that route —
#: `/calendar/event/<int:event_id>/signups/export/` — and it dumps Name and
#: Email for every member signed up to an event, which is the same class of
#: bulk member data as the directory and user-list exports sitting in this list
#: already. It could not be added here as a prefix. It had also been a hard 500
#: since v3.9.1 (an import of a module that never existed, fixed in v3.17.3),
#: so it is about to be reachable in production for the first time.
#:
#: Matching on the resolved URL name is parameter-agnostic, survives a route
#: being moved or re-pathed, and makes this read as a list of *capabilities*
#: instead of a list of strings.
RESTRICTED_EXPORT_VIEWS = frozenset({
    'export_directory',
    'export_activity_logs',
    'export_user_list',
    'export_kai_reports_csv',
    'export_service_csv',
    'event_signup_export',            # v3.17.5 — see above
    'admin_v2_clear_honeypot_logs',   # Bulk delete — too destructive from abroad
})

#: Retained as belt-and-braces for anything that does not resolve to a named
#: route. A path with no `name=` cannot be matched by the set above, so this
#: stays rather than being deleted. Both checks are OR'd.
#:
#: NOTE: `/officers/db-dump/` is kept deliberately even though **no such route
#: exists anywhere in `src/urls.py`** (verified 07-30-26). It costs one string
#: comparison and it fails closed if the endpoint is ever added back without
#: anyone remembering this file.
RESTRICTED_PATH_PREFIXES = (
    '/officers/db-dump/',
)


def _restricted_view_name(request):
    """
    The restricted URL name this request resolves to, or None.

    Middleware runs before Django's handler sets `request.resolver_match`, so
    the resolve has to happen here. `resolve()` reads the cached resolver, so
    this is a dict lookup rather than a re-parse — and it only runs for sessions
    already flagged as non-US, which is the rare path.
    """
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    return match.url_name if match.url_name in RESTRICTED_EXPORT_VIEWS else None


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

                # Block restricted endpoints — by resolved URL name (v3.17.5),
                # falling back to the prefix list for unnamed routes.
                view_name = _restricted_view_name(request)
                if view_name or any(path.startswith(p) for p in RESTRICTED_PATH_PREFIXES):
                    geo = request.session.get('login_geo', {})
                    country = request.session.get('login_geo_country', 'Unknown')
                    city = request.session.get('login_geo_city', '')
                    location = f"{city}, {country}" if city else country

                    logger.warning(
                        f"GEO_BLOCK: User '{request.user.user_id}' attempted to access "
                        f"restricted export '{path}' (view={view_name or 'unnamed'}) "
                        f"from non-US location ({location}). Blocked."
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
