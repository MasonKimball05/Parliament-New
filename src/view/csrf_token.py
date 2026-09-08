"""
Silent CSRF token refresh — the client-side half of the v3.26.5 mobile
bfcache fix's known residual case.

v3.26.5 (base.html) reloads the whole page when `pageshow` fires with
`persisted: true` (a bfcache-restored page, most commonly a phone
returning from the app-switcher or a native file-picker sheet), because a
restored page can be holding a CSRF token that's gone stale or been
dropped by the browser's own form-state restoration. That reload is
skipped whenever any text input/textarea/contenteditable holds unsaved
content, to avoid wiping a half-written draft — an explicit, accepted
trade-off at the time.

That trade-off turned out to bite hardest on exactly the pages most
likely to trigger a bfcache restore in the first place: an upload form
with a required title/description field ahead of the file picker. Typing
the title *is* normal use of the form, so the reload-skip guard fires on
essentially every real submission, and the token never gets refreshed.
Reproduced live 09-01-26 on `/committee/<code>/upload-document/` — see
`changelogs/v3.28.4.md`.

This endpoint lets `base.html` refresh just the token, in place, without
navigating away — so typed input and any selected file survive, and the
token is current either way.

⚠️ TEMPORARY DIAGNOSTIC LOGGING — v3.29.26, 09-08-26. v3.29.25 shipped,
deployed, and the identical failure (`posted_token_present=False`)
recurred on a live mobile retest with no visible toast and "almost no
time at all" before the 403 — a pattern consistent with the client-side
safety net (`refreshCsrfToken()` in base.html) never actually reaching
this endpoint at all, but that can't be confirmed from the server side
alone. This endpoint is the ONLY place that JS function ever talks to the
server, so a hit here IS proof the safety net ran; silence around a
failure's timestamp is proof it didn't. Logs to the `security` logger
(same file/format as `csrf_failure.py`, which the mobile 403 itself
already logs to) rather than `ActivityLog` — this is throwaway debugging
signal, not an audit-trail event, and doesn't belong in the formal
activity log members can be shown. Remove this logging once the root
cause is confirmed and fixed.

⚠️ EXTENDED — v3.29.28, 09-08-26. `refreshCsrfToken()` in base.html has
TWO independent callers — the `pageshow` handler (proactive, fires on a
bfcache restore, unrelated to any click) and the submit-safety-net
listener (fires immediately before its own resubmit) — and both hit this
exact endpoint, producing an identical-looking log line either way. The
09-08 repro showed a successful refresh 165ms before a failure whose
`csrf_failure.py` line read `is_js_resubmit=False` — and v3.29.27's own
code proves the submit-listener's resubmit can never reach
`form.submit()` with an empty token field (every path either sets a
real value or bails out via `P.toast()` first), so that resubmit is
provably not what failed. Whether the .222 refresh was even RELATED to
that failure was still a guess. `source` (`'pageshow'` or `'submit'`,
sent as a query param since this is a GET) closes that gap: if the
refresh immediately preceding a failure reads `source=pageshow`, the
failing POST is confirmed unrelated to anything this JS does — a raw,
un-intercepted native submission — a different bug class than every
theory tested so far. Remove alongside the rest of this temporary
logging once the root cause is confirmed and fixed.

⚠️ EXTENDED AGAIN — v3.29.29, 09-08-26. The 09-08 retest came back
`source=submit` — a genuine submit event WAS intercepted and ITS OWN
refresh succeeded — yet the failure it preceded still read
`is_js_resubmit=False`. Given v3.29.28's proof that this listener's own
resubmit can't reach `form.submit()` empty, that combination can only
mean a SECOND, separate submission produced the actual failure — one
this endpoint has no visibility into, because it never asked for a
token refresh at all. `base.html`'s pre-existing double-submit
protection script silently blocks a genuine second `submit` DOM event on
a form already marked as submitted; it now also fires a bare GET here
with `?source=duplicate_blocked` (no token use, purely to produce a log
line) the moment that happens, so a hit here around a failure's
timestamp is direct evidence a second submission was attempted — instead
of inferring it from silence, same reasoning as every prior round.
Remove alongside the rest of this temporary logging once the root cause
is confirmed and fixed.

⚠️ EXTENDED AGAIN — v3.29.29, same day, second addendum. Mason pasted an
11-minute log window around the `source=submit` failure above (not just
the 3 lines immediately around it) — it contains exactly one refresh and
one failure and nothing else related to this upload attempt at all. No
second failure line, no success, no trace whatsoever of what the marked
resubmit (which the code guarantees carries a real token if it ever
reaches `form.submit()`) actually did. `base.html`'s submit listener now
also fires `?source=about_to_submit` — via `fetch(..., {keepalive:
true})`, specifically because a plain `fetch()` can be aborted by the
page navigation `form.submit()` itself immediately triggers — right
before that `form.submit()` call. A hit here with nothing following it
server-side (no success, no `is_js_resubmit=True` failure) would prove
the code reaches the submit call but the browser never actually delivers
the resulting request — a client-side/WebKit-level failure specific to
resubmitting a multipart file form, outside anything server-side logging
can diagnose further. Remove alongside the rest of this temporary
logging once the root cause is confirmed and fixed.
"""
import logging

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET

from src.utils.security_utils import get_client_ip

logger = logging.getLogger('security')

_MAX_UA_LENGTH = 200


@ensure_csrf_cookie
@require_GET
def csrf_token_refresh(request):
    """
    Return a CSRF token valid for the current session, and (via
    `ensure_csrf_cookie`) make sure the cookie backing it is actually set
    on the response — a page that only ever GETs this via `fetch()` must
    not depend on some earlier page load having set the cookie already.

    No `@login_required`: CSRF applies to anonymous sessions too (a public
    contact form, for instance), and this endpoint discloses nothing a
    normal page render doesn't already put in `{% csrf_token %}`/the
    `<meta name="csrf-token">` tag.
    """
    csrf_cookie_name = getattr(settings, 'CSRF_COOKIE_NAME', 'csrftoken')
    had_csrf_cookie_before = csrf_cookie_name in request.COOKIES

    token = get_token(request)

    user = getattr(request, 'user', None)
    if user is not None and getattr(user, 'is_authenticated', False):
        user_desc = f'{getattr(user, "user_id", "?")} ({getattr(user, "username", "?")})'
    else:
        user_desc = 'anonymous'

    # ⚠️ TEMPORARY — v3.29.28, see module docstring. Which of the two JS
    # callers triggered this hit — 'pageshow' (proactive, no click
    # involved) or 'submit' (about to resubmit). Read from the query
    # string, not trusted beyond a diagnostic label: an unrecognized or
    # absent value just reads as '-' rather than raising.
    source = request.GET.get('source') or '-'

    # TEMPORARY — see module docstring. `bool(token)` not the token value
    # itself, same reasoning as csrf_failure.py: a security log is an
    # asset, don't put secrets in it just to answer "did this work."
    logger.info(
        'CSRF token refresh called | source=%s | referer=%s | had_csrf_cookie_before=%s | '
        'returned_a_token=%s | ip=%s | user=%s | ua=%s',
        source,
        request.META.get('HTTP_REFERER', '-'),
        had_csrf_cookie_before,
        bool(token),
        get_client_ip(request) or 'unknown',
        user_desc,
        request.META.get('HTTP_USER_AGENT', '')[:_MAX_UA_LENGTH],
    )

    response = JsonResponse({'csrfToken': token})
    # Never cache a token response — nothing downstream should treat this
    # as reusable beyond the one refresh that asked for it.
    response['Cache-Control'] = 'no-store'
    return response
