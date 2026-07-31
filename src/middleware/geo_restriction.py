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
#: v3.17.7 — AND WHY NAMES ARE STILL NOT ENOUGH
#: --------------------------------------------
#: Matching on URL name fixed "the parameter is in the middle of the path". It
#: does not fix the other shape: **an export that is a MODE of an ordinary page
#: rather than a route of its own.** Three exist:
#:
#:   * `poll_results`         + `?export=csv` → respondent names and answers
#:   * `admin_v2_audit_log`   + `?export=csv` → actor, target, detail, IP
#:   * `bulk_actions_kai_reports` POST `bulk_action=export_csv`
#:
#: Only the third can live in this set, because its URL name IS the export —
#: it is a POST-only bulk-action endpoint. Listing the other two would geo-block
#: the poll results screen and the audit log viewer entirely, not just their
#: exports, so those two are guarded **inside the view** against
#: `request.geo_suspicious`, which this middleware sets on every request. The
#: information was always here; only the enforcement assumed route granularity.
#: Search for `geo_export_blocked` to find those guards.
RESTRICTED_EXPORT_VIEWS = frozenset({
    'export_directory',
    'export_activity_logs',
    'export_user_list',
    'export_kai_reports_csv',
    'export_service_csv',
    'event_signup_export',            # v3.17.5 — see above
    'bulk_actions_kai_reports',       # v3.17.7 — POST-only, export_csv branch
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


def _block_response(request, view_name):
    """
    The 403 page and the security-log line for a blocked export.

    v3.17.7: shared by the middleware and by `geo_export_blocked()` so a
    view-level guard is indistinguishable from a middleware block — same page,
    same log format, one place to change either.
    """
    path = request.path_info
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


def geo_export_blocked(request):
    """
    A 403 response if this request must not pull a bulk export, else ``None``.

    v3.17.7 — for the exports that are a **query-parameter mode of an ordinary
    page** rather than a route of their own. `RESTRICTED_EXPORT_VIEWS` cannot
    reach those: the URL name belongs to the page, so listing it would block the
    page too. Call this at the top of the export branch instead::

        if request.GET.get('export') == 'csv':
            blocked = geo_export_blocked(request)
            if blocked:
                return blocked
            return _export_csv(...)

    Safe to call from anywhere — `request.geo_suspicious` is set by
    `GeoRestrictionMiddleware` on every request, and `getattr` keeps this
    working in tests that build a request without the middleware.
    """
    if not getattr(request, 'geo_suspicious', False):
        return None
    view_name = getattr(getattr(request, 'resolver_match', None), 'url_name', None)
    return _block_response(request, view_name)


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
                    return _block_response(request, view_name)

                # Log all authenticated requests from flagged sessions (non-GET only, to reduce noise)
                if request.method not in ('GET', 'HEAD'):
                    logger.warning(
                        f"GEO_SUSPICIOUS: User '{request.user.user_id}' ({request.method}) "
                        f"{path} — session flagged as non-US login"
                    )

        return self.get_response(request)
