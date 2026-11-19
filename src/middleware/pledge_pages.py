"""
v3.50.0 — pledge page restrictions apply to EVERY page, not just three.

The education dashboard lets the VPE "Restrict a Page" for pledges by its URL
name (`PledgePageRestriction`). Until now only three views carried the
`@pledge_page_allowed` decorator (`chapter_documents`, `slating_apply`,
`slating_results`), so a restriction added for any other page — the
dashboard's own example is "locking the songbook until Phase 2" — saved
fine, showed in the table, and did nothing. No error, no log.

This middleware does the check for every resolved page, from the same rule
(`PledgePageRestriction.is_allowed`, cached 5 minutes per page), so a row is
always enforced. The decorator stays on its three views as a second layer.

Cost: nothing for non-pledges; one cache read per pledge request.

Pages a pledge must always reach (signing in and out, changing a password,
2FA, the PWA files) are in `ALWAYS_ALLOWED`; the dashboard also refuses to
save a restriction for them, so a typo can't lock every pledge out.
"""
from django.shortcuts import render

#: url_names never blocked, whatever rows exist.
ALWAYS_ALLOWED = frozenset({
    'login', 'logout', 'home',
    'change_password', 'forced_password_change', 'check_default_password',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
    'csrf_token_refresh', 'push_subscribe', 'push_unsubscribe',
    'service_worker',  # v3.50.1: the docstring promised the PWA files; this one was missing
    'apple_app_site_association', 'assetlinks',  # mobile app-link metadata
    # v4.4.0: anyone may delete their own account (App Store guideline 5.1.1(v)),
    # and the public privacy/terms/support pages are linked from sign-in.
    'delete_account', 'privacy_policy', 'terms', 'support',
})

#: url_name prefixes never blocked (2FA setup/verify, passkeys, onboarding,
#: and the mobile app's plumbing: sign-in/sign-out, `me/`, devices, push token
#: and websocket tickets, which every member needs for the app to work at all).
ALWAYS_ALLOWED_PREFIXES = ('two_factor_', 'passkey_', 'onboarding', 'mobile_auth_', 'mobile_me',
                           'mobile_device', 'mobile_push_token', 'mobile_ws_ticket', 'mobile_account_delete')


def is_always_allowed(url_name):
    return url_name in ALWAYS_ALLOWED or url_name.startswith(ALWAYS_ALLOWED_PREFIXES)


class PledgePageRestrictionMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        # On /api/mobile/ the signed-in user is the Bearer device's, not the
        # (anonymous) session's — see src/mobile/http.py.
        from src.mobile.http import effective_user, is_mobile_api, mobile_json_error
        user = effective_user(request)
        if user is None or not user.is_authenticated or not user.is_pledge:
            return None
        match = getattr(request, 'resolver_match', None)
        url_name = getattr(match, 'url_name', None)
        if not url_name or is_always_allowed(url_name):
            return None
        from src.models.education import PledgePageRestriction
        phase = getattr(user, 'pledge_phase', None) or 'all'
        # v4.4.15 (pentest F01): a restriction on a section's page also covers the
        # routes under its path. Restricting `member_directory` (directory/) used
        # to leave `export_directory` (directory/export/: everyone's email and
        # phone as CSV/XLSX) and `profile_card` (directory/<id>/card/) open,
        # because the match was on the exact url_name alone.
        for name in (url_name, *section_parents(url_name)):
            if not PledgePageRestriction.is_allowed(name, phase):
                break
        else:
            return None
        if is_mobile_api(request):
            return mobile_json_error('pledge_restricted', 403)
        return render(request, 'errors/pledge_restricted.html', status=403)


_RESTRICTABLE = None
_PARENTS = None


def _literal_prefix(route):
    """The part of a route before its first parameter: 'directory/<str:user_id>/card/' → 'directory/'."""
    return route.split('<', 1)[0]


def section_parents(url_name):
    """
    v4.4.15 — the named pages whose path contains this route's path: for
    'export_directory' (directory/export/) that is ('member_directory',) since
    'directory/' is a prefix of it. Only parameter-free routes count as a
    section, the empty path (home) never does, and always-allowed pages are
    left out so they can never block anything beneath them.
    """
    global _PARENTS
    if _PARENTS is None:
        from django.urls import URLPattern, URLResolver, get_resolver
        routes = {}

        def walk(patterns, prefix=''):
            for p in patterns:
                if isinstance(p, URLResolver):
                    walk(p.url_patterns, prefix + str(p.pattern))
                elif isinstance(p, URLPattern) and p.name and '^' not in str(p.pattern):
                    routes.setdefault(p.name, prefix + str(p.pattern))
        walk(get_resolver().url_patterns)
        sections = {name: route for name, route in routes.items()
                    if route and '<' not in route and route.endswith('/')
                    and not is_always_allowed(name)}
        _PARENTS = {}
        for name, route in routes.items():
            lit = _literal_prefix(route)
            _PARENTS[name] = tuple(sorted(
                parent for parent, sroute in sections.items()
                if parent != name and lit.startswith(sroute) and route != sroute))
    return _PARENTS.get(url_name, ())


def restrictable_url_names():
    """
    Every page name a restriction can apply to: all named routes, minus the
    always-allowed ones and pages pledges can never open anyway (admin v2,
    honeypots). Used to refuse a typo on the dashboard ("songbok" would
    otherwise save and silently restrict nothing).
    """
    global _RESTRICTABLE
    if _RESTRICTABLE is None:
        from django.urls import URLPattern, URLResolver, get_resolver

        names = set()

        def walk(patterns):
            for p in patterns:
                if isinstance(p, URLResolver):
                    walk(p.url_patterns)
                elif isinstance(p, URLPattern) and p.name:
                    names.add(p.name)

        walk(get_resolver().url_patterns)
        _RESTRICTABLE = sorted(
            n for n in names
            if not is_always_allowed(n)
            and not n.startswith(('admin_v2_', 'honeypot_'))
            and '/' not in n
        )
    return _RESTRICTABLE
