"""
JS-resubmit response envelope — v3.29.33.

Context: `templates/base.html`'s global submit listener (v3.29.24-30)
resubmits any POST form with a file input via `fetch()` + `FormData`
instead of a second native `form.submit()` — that native second call was
proven, 09-08-26, to silently serialize an EMPTY body in WebKit browsers
(see `changelogs/v3.29.28.md`'s addenda for the full diagnostic history,
and `changelogs/v3.29.33.md` for this fix).

That fix introduced a new problem, found in the 09-09-26 auto-run
review. `fetch()`'s default `redirect: 'follow'` makes the browser
transparently execute the redirect TARGET request *inside* the fetch
call, invisibly to the calling JS — which only ever sees the final
response. That target request is a real Django request/response cycle,
and `base.html` renders `{% for message in messages %}` on every page,
so Django's one-shot session flash message gets consumed by that
invisible hop before the JS's own visible `window.location.href`
navigation ever requests the page a second time. Verified directly with
the Django test client in
`src/tests/security/test_js_resubmit_envelope.py` — a flash message is
present on the first render after a redirect and gone on a second one,
regardless of whether anything ever reads that first render's body. The
same swallowing happens for a validation error that re-renders the form
in place (200, no redirect): the JS never reads that response's HTML
body at all, only `response.ok`, so the error the member needed to see
vanished entirely.

THE FIX. For a POST request carrying the `X-Parliament-Resubmit` header
— sent ONLY by that one `fetch()` call site in `base.html`, never by a
regular navigation, and deliberately a DIFFERENT header from the
pre-existing `X-Requested-With: XMLHttpRequest` that ~13 other views
already branch on for their own, unrelated AJAX JSON responses — this
middleware intercepts the response after the view has run, before
`MessageMiddleware` decides what to persist, and hands back a small
JSON envelope instead of raw HTML or an HTTP redirect:

    {"redirect": "<url>" | null, "messages": [{"text": str, "level": str}, ...]}

`redirect` carries the `Location` header's value if the view returned a
3xx, or `null` if the view rendered in place (the validation-error
case, still 200). `messages` is whatever was queued via
`django.contrib.messages` during this same request — read directly from
`request._messages`, which is still populated in memory at this point
regardless of whether a template already iterated it. Reading
(iterating) it here ALSO marks it used, exactly like `{% for message in
messages %}` does, so `MessageMiddleware`'s own `process_response` (which
runs AFTER this one — see the ordering note on the class below) will not
persist these messages a second time for whatever page the browser
later, visibly, navigates to.

This makes the browser's `fetch()` see exactly ONE HTTP response either
way — never a 3xx it would silently follow — so the invisible-consumption
problem cannot recur, and there is no message left to lose: `fetch()`
never executes the redirect target at all.

SCOPE. Only engages for the one header, method, and status range this
exists for (POST; 200 or a 3xx). Everything else — GET requests, other
methods, 4xx/5xx responses, and any response that is already JSON —
passes through completely unchanged, including the ~13 existing
`X-Requested-With`-based JSON endpoints, which use a different header
and are untouched by this. Deliberately NOT scoped to a hardcoded list
of "the upload views" — any current or future form this app routes
through `base.html`'s file-input resubmit path is covered automatically,
the same "enumerate the mechanism, not the instances" reasoning
`CLAUDE.md` records for this codebase's other guards.
"""
from django.http import JsonResponse

#: WSGI header key for `X-Parliament-Resubmit` (Django uppercases,
#: replaces `-` with `_`, and prefixes `HTTP_` for ordinary headers).
_RESUBMIT_HEADER_KEY = 'HTTP_X_PARLIAMENT_RESUBMIT'

#: Statuses this middleware treats as "a redirect the client asked us
#: to relay" rather than passing through for the browser to follow.
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class JsResubmitEnvelopeMiddleware:
    """
    See module docstring for what this does and why.

    ⚠️ ORDERING. This must run BEFORE `MessageMiddleware` in the response
    phase — Django calls `process_response`/`__call__`'s post-`get_response`
    code in REVERSE of `settings.MIDDLEWARE` order, so "before
    MessageMiddleware in the response phase" means this class must be
    listed AFTER `django.contrib.messages.middleware.MessageMiddleware`
    in `MIDDLEWARE`. Get this backwards and messages this middleware
    reads have already been persisted forward by `MessageMiddleware` as
    "still unread," and the fix regresses into showing every message
    TWICE (once via this envelope, once again on whatever page the
    browser's own navigation lands on) rather than the zero times it was
    showing before. `src/tests/security/test_js_resubmit_envelope.py::
    TheMiddlewareOrderingTests` pins the position in `settings.MIDDLEWARE`
    directly so this can't drift silently.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.method != 'POST' or not request.META.get(_RESUBMIT_HEADER_KEY):
            return response

        is_redirect = response.status_code in _REDIRECT_STATUSES
        if not is_redirect and response.status_code != 200:
            # 4xx/5xx (permission denied, server error, etc.) pass
            # through unchanged — the JS's existing `!response.ok`
            # branch already shows a generic failure toast for these,
            # and there is nothing view-specific to relay.
            return response

        # Don't double-wrap a view that already answers this header (or
        # any caller) with JSON of its own.
        if response.get('Content-Type', '').split(';')[0].strip() == 'application/json':
            return response

        redirect_to = response.get('Location') if is_redirect else None

        payload_messages = []
        # `request._messages` is set up by MessageMiddleware's own
        # request-phase processing and is still populated here
        # regardless of whether a template already rendered (and so
        # already iterated) it during this same request — iterating it
        # again is idempotent for OUR purposes and is what marks it used
        # for MessageMiddleware's process_response, which runs after
        # this one (see the ordering note above).
        messages_storage = getattr(request, '_messages', None)
        if messages_storage is not None:
            for message in messages_storage:
                payload_messages.append({
                    'text': str(message),
                    'level': message.tags or 'info',
                })

        return JsonResponse({
            'redirect': redirect_to,
            'messages': payload_messages,
        })
