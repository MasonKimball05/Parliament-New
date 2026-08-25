"""
Custom CSRF failure view — v3.26.3.

Django's default `CSRF_FAILURE_VIEW` logs exactly one thing: the reason
string ("CSRF token missing.", "CSRF cookie not set.", "CSRF token
incorrect.", or a Referer/Origin-checking message). That was the entire
content of the 08-25-26 prod log lines Mason was trying to diagnose from —
enough to know CSRF failed, nothing about which of the several different
mechanisms that can produce it actually fired for a given visitor.

This view logs a richer, single pipe-delimited line per failure (matching
the `security` logger's existing style — see `csp_report.py`'s "CSP
violation | ..." line) and then renders the SAME styled 403 page the rest of
the app uses, via `_render_403`, rather than Django's generic default
template.

⚠️ WHAT IS DELIBERATELY NOT LOGGED: the actual CSRF token or cookie VALUE,
from either the request or the session. Only presence/absence (booleans) and
metadata (lengths, headers). A security log is itself an asset — logging the
secret you're trying to protect defeats the point of protecting it.

Fields, and what each is FOR:
  reason           — Django's own diagnosis (the one thing prior logging had)
  path, method      — which page/action failed
  has_csrf_cookie   — was `csrftoken` present in the request at all? If
                      False, rules out "stale token, valid cookie" theories
                      (bfcache, cross-visitor cache) — the cookie itself was
                      never there, which points at first-time-visitor /
                      cookies-blocked / cookie-domain-mismatch causes instead.
  has_session_cookie — same question for `sessionid`. If this is ALSO False
                      on an otherwise-normal-looking request, the visitor's
                      cookies are being dropped or blocked wholesale (not a
                      CSRF-specific problem) — a client-side or intermediary
                      cause, not anything Django is doing wrong per se.
  posted_token_present — was `csrfmiddlewaretoken` (or the X-CSRFToken
                      header) actually IN the submitted request? If True and
                      has_csrf_cookie is also True, Django's message would be
                      "CSRF token incorrect." (mismatch), not "missing" — so
                      seeing True here on a "missing" failure would itself be
                      a signal something is inconsistent and worth a second
                      look.
  referer, origin   — do they match the site at all? A cross-origin value
                      here would point at something embedding/framing the
                      form rather than at a caching/staleness explanation.
  cf_ray            — Cloudflare stamps every request it proxies with this
                      header. PRESENT means the request came through
                      Cloudflare as expected. ABSENT on a request that should
                      have gone through Cloudflare points at the same
                      direct-to-origin access class of issue as the
                      `144.126.251.9` DisallowedHost noise from the same day
                      — worth cross-referencing by timestamp.
  sec_fetch_site    — modern browsers send this on (almost) every request;
                      "same-origin"/"same-site" is normal, "cross-site" or
                      "none" (typed URL / bookmark / bfcache restore in some
                      browsers) is exactly the kind of thing that would
                      distinguish "stale cached page" from "legitimate
                      same-site submit that just failed for cookie reasons."
  user_agent        — truncated; mobile Safari's aggressive bfcache is the
                      documented repeat offender in this app (see
                      `_render_403`'s comment) — this is what would confirm
                      or rule that out across a batch of these log lines.
  client_ip         — via the shared `get_client_ip`, i.e. the same
                      IP-extraction this app uses everywhere else (honeypot,
                      rate limiters, ActivityLog), not a fifth copy of it.
  user              — if the request is (somehow) authenticated despite the
                      CSRF failure, who — lets a report of "it happened to
                      me" be matched against a specific log line.
"""
import logging

from django.conf import settings

from src.middleware.security import _render_403
from src.utils.security_utils import get_client_ip

logger = logging.getLogger('security')

_MAX_UA_LENGTH = 200


def csrf_failure(request, reason=""):
    """
    `CSRF_FAILURE_VIEW` target (see Parliament/settings.py). Logs a
    diagnostic line to the `security` logger, then renders this app's own
    styled 403 page — not Django's generic default one — via `_render_403`,
    which already sets `Cache-Control: no-store` (see v3.26.2; belt and
    suspenders with the middleware-wide default added there).
    """
    csrf_cookie_name = getattr(settings, 'CSRF_COOKIE_NAME', 'csrftoken')
    session_cookie_name = getattr(settings, 'SESSION_COOKIE_NAME', 'sessionid')

    has_csrf_cookie = csrf_cookie_name in request.COOKIES
    has_session_cookie = session_cookie_name in request.COOKIES

    # Presence only — never the value. Reading request.POST here is safe:
    # CsrfViewMiddleware has already fully parsed the body (that parsing is
    # what produced the failure in the first place), so this does not
    # trigger a second, separate multipart read.
    try:
        posted_token_present = bool(request.POST.get('csrfmiddlewaretoken')) or bool(
            request.META.get('HTTP_X_CSRFTOKEN')
        )
    except Exception:
        # A malformed body is itself part of what CSRF checking guards
        # against — do not let inspecting it raise out of an error handler.
        posted_token_present = None

    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        user_desc = f'{getattr(user, "user_id", "?")} ({getattr(user, "username", "?")})'
    else:
        user_desc = 'anonymous'

    user_agent = request.META.get('HTTP_USER_AGENT', '')[:_MAX_UA_LENGTH]

    logger.warning(
        'CSRF failure | reason=%s | path=%s | method=%s | '
        'has_csrf_cookie=%s | has_session_cookie=%s | posted_token_present=%s | '
        'referer=%s | origin=%s | cf_ray=%s | sec_fetch_site=%s | '
        'ip=%s | user=%s | ua=%s',
        reason,
        request.path,
        request.method,
        has_csrf_cookie,
        has_session_cookie,
        posted_token_present,
        request.META.get('HTTP_REFERER', '-'),
        request.META.get('HTTP_ORIGIN', '-'),
        request.META.get('HTTP_CF_RAY', '-'),
        request.META.get('HTTP_SEC_FETCH_SITE', '-'),
        get_client_ip(request) or 'unknown',
        user_desc,
        user_agent or '-',
    )

    return _render_403(request, reason)
