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
"""
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET


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
    response = JsonResponse({'csrfToken': get_token(request)})
    # Never cache a token response — nothing downstream should treat this
    # as reusable beyond the one refresh that asked for it.
    response['Cache-Control'] = 'no-store'
    return response
