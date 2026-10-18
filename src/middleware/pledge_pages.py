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
})

#: url_name prefixes never blocked (2FA setup/verify, passkeys, onboarding).
ALWAYS_ALLOWED_PREFIXES = ('two_factor_', 'passkey_', 'onboarding')


def is_always_allowed(url_name):
    return url_name in ALWAYS_ALLOWED or url_name.startswith(ALWAYS_ALLOWED_PREFIXES)


class PledgePageRestrictionMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated or not user.is_pledge:
            return None
        match = getattr(request, 'resolver_match', None)
        url_name = getattr(match, 'url_name', None)
        if not url_name or is_always_allowed(url_name):
            return None
        from src.models.education import PledgePageRestriction
        phase = getattr(user, 'pledge_phase', None) or 'all'
        if PledgePageRestriction.is_allowed(url_name, phase):
            return None
        return render(request, 'errors/pledge_restricted.html', status=403)


_RESTRICTABLE = None


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
