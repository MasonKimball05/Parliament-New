"""
Every page a member can be sent to must be reachable by clicking (v3.22.0).

⚠️ WHAT THIS IS FOR.

`/my-attendance/` is a 224-line personal attendance dashboard — history, stats,
and a comparison against the chapter average. It has existed for a long time.
**No template linked to it.** The only way to reach it was to type the URL.

That is not a new failure here. CLAUDE.md records the same shape twice already:

* the **calendar Subscribe button**, invisible for months because it was gated on
  a feature flag that was never seeded — the endpoints worked the whole time;
* the **guide pages** whose buttons pointed at routes that did not exist, three
  of them live 500s, found by v3.16.3's template URL-name scanner.

Both of those were found by accident. `test_hardcoded_urls` catches a link that
goes nowhere; `TemplateUrlNameTests` catches a `{% url %}` naming a route that
does not exist. **Nothing caught the opposite direction — a route with no link.**

> **A feature nobody can reach is indistinguishable from a feature that was
> never built**, and it costs more, because it also has to be maintained.

⚠️ WHY THIS IS A RATCHET AND NOT A CLEAN ASSERTION.

`KNOWN_ORPHANS` below lists routes that are unreachable today and that I did not
want to decide about in the same change that found them — the same call made for
`is_ip_honeypot_banned()` in v3.21.7. **The set may only ever shrink.** Adding to
it is how a ratchet becomes a rug; the test says so and
`test_the_known_orphan_list_only_shrinks` pins the count.

⚠️ NAMED LIMITATION, and it is the honest one: this scans for `{% url %}` in
templates and `reverse()` / `redirect()` in Python. A page reached by a URL built
in JavaScript is invisible to it — but `test_hardcoded_urls` already fails the
build on string-built site paths, so that route is closed from the other side.
The two tests are complements, not duplicates.
"""
import inspect
import os
import re

from django.test import SimpleTestCase
from django.urls import get_resolver

TEMPLATE_ROOT = 'templates'
PYTHON_ROOT = 'src'

#: Unreachable and deliberately so. Each entry needs a reason, and the reason has
#: to be about how the page is reached — not "it is fine".
EXEMPT = {
    # Reached from a link in an email, not from the site.
    'two_factor_recovery_confirm',
    'password_reset_done',
    'password_reset_complete',
    'confirm_email_change',
    # Django admin plumbing; Django itself does the redirecting.
    'admin_login_redirect',
    # Deliberately unauthenticated pages meant to be shared as a bare URL
    # (rush, parents, nationals). They are not in the member nav on purpose.
    'public_songbook',
    'public_song_detail',
    # Scanners are supposed to find these by guessing, and a member never
    # should. Linking one would defeat the entire mechanism.
    # (matched by prefix below, listed here for the reader)
}

#: Prefixes exempt for the same reason as the honeypots above.
EXEMPT_PREFIXES = ('honeypot_', 'debug_')

#: ⚠️ RATCHET. Unreachable today, decision deferred, **may only shrink.**
#:
#: ⚠️ v3.25.0 — IT SHRANK, 4 → 2, AND THAT IS WHAT THIS LIST IS FOR.
#: `make_event` and `manage_event` (singular) were stubs — each was
#: `return render(request, '<name>.html', {})` with no context — superseded by
#: `create_event` and `manage_events` (plural). v3.22.0 recorded them here
#: rather than deleting them inside a change about something else, which was the
#: right call; a day later, in a diff of its own, they were deleted: two views,
#: four routes (including two legacy underscore redirects) and 238 lines of
#: template.
#:
#: The reason it was worth doing rather than tolerating: `manage_event.html` was
#: 229 lines of a real-looking announcements page whose view passed `{}`, so
#: every `{% if %}` in it was false and it could only ever render as an empty
#: shell. A future officer finds that file, edits it, and nothing changes
#: anywhere on the site.
KNOWN_ORPHANS = {
    'slating_periods',
    'view_passed_legislation_document',
}


def _referenced_route_names():
    """Every route name something in the codebase can send a user to."""
    found = set()

    for root, _dirs, files in os.walk(TEMPLATE_ROOT):
        if 'archive' in root:
            continue
        for name in files:
            if not name.endswith('.html'):
                continue
            with open(os.path.join(root, name), encoding='utf-8', errors='ignore') as fh:
                body = fh.read()
            found |= set(re.findall(r"\{%\s*url\s+'([a-z0-9_]+)'", body))
            found |= set(re.findall(r'\{%\s*url\s+"([a-z0-9_]+)"', body))

    for root, _dirs, files in os.walk(PYTHON_ROOT):
        for name in files:
            if not name.endswith('.py'):
                continue
            with open(os.path.join(root, name), encoding='utf-8', errors='ignore') as fh:
                body = fh.read()
            for call in ('reverse', 'reverse_lazy', 'redirect'):
                found |= set(re.findall(call + r"\(\s*['\"]([a-z0-9_]+)['\"]", body))

    return found


def _page_routes():
    """
    Named routes whose view renders a template.

    An API endpoint returning `JsonResponse` is not a page and is not expected
    to be linked — it is *called*, which is a different thing and one this test
    has no business asserting about.
    """
    for pattern in get_resolver().url_patterns:
        name = getattr(pattern, 'name', None)
        callback = getattr(pattern, 'callback', None)
        if not name or callback is None:
            continue
        try:
            source = inspect.getsource(callback)
        except (OSError, TypeError):
            continue
        if 'render(' not in source:
            continue
        yield name


class EveryPageIsReachableTests(SimpleTestCase):

    def test_the_scan_finds_a_plausible_number_of_pages(self):
        """
        A scan that matches nothing passes every assertion below vacuously —
        the guard `test_singleton_rows` and `test_ip_sentinel` both needed.
        """
        pages = set(_page_routes())
        self.assertGreater(len(pages), 100, f'only found {len(pages)} page routes')

    def test_the_reference_scan_finds_the_links_we_know_about(self):
        """The other half of the same guard: an empty reference set would make
        EVERY page look orphaned, which reads as a catastrophic finding and is
        really a broken regex."""
        referenced = _referenced_route_names()
        for known in ('home', 'profile', 'calendar', 'my_excuses', 'my_attendance'):
            self.assertIn(known, referenced)

    def test_no_page_is_unreachable(self):
        referenced = _referenced_route_names()
        orphans = sorted(
            name for name in _page_routes()
            if name not in referenced
            and name not in EXEMPT
            and name not in KNOWN_ORPHANS
            and not name.startswith(EXEMPT_PREFIXES)
        )
        self.assertEqual(
            orphans, [],
            'These pages render a template and nothing links to them, so the '
            'only way to reach them is to type the URL:\n  '
            + '\n  '.join(orphans)
            + '\n\nLink it, delete it, or add it to EXEMPT with a reason about '
              'HOW the page is reached. Do NOT add it to KNOWN_ORPHANS — that '
              'set is a ratchet for things that were already broken when this '
              'test was written, and it may only shrink.',
        )

    def test_the_known_orphan_list_only_shrinks(self):
        """
        ⚠️ THE POINT OF THE RATCHET. `KNOWN_ORPHANS` exists so this test could
        ship without also deciding the fate of four pre-existing orphans. If it
        is allowed to grow, it stops being a debt register and becomes a place
        to put things so the build goes green — which is the failure mode
        v3.20.1's `test_fetch_error_visibility` ratchet was written against.

        Lower this number when you delete or link one. Never raise it.

        4 → 2 in v3.25.0: `make_event` and `manage_event` were deleted.
        """
        self.assertLessEqual(
            len(KNOWN_ORPHANS), 2,
            'KNOWN_ORPHANS grew. Whatever you were about to add belongs in '
            'EXEMPT with a reason, or wants linking, or wants deleting.',
        )

    def test_my_attendance_specifically_is_linked(self):
        """
        The instance that prompted the module. Kept as a named test rather than
        left to the general assertion because the general one would also pass if
        somebody added it to KNOWN_ORPHANS, and this one would not.
        """
        self.assertIn('my_attendance', _referenced_route_names())
