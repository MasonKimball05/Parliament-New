"""
A hardcoded site path must resolve to a real route.

WHY THIS EXISTS (v3.17.6)
-------------------------
Commit 50ac888 standardised site paths on hyphens (`/user_list/` ->
`/user-list/`, and eight more). The URL **names** did not change, so every
`{% url %}` and `reverse()` followed automatically and the site looked fine.

Nothing else followed. Seven hardcoded strings kept the old spelling, and the
two worst were in tests:

  * `test_query_narrowing.test_pages_do_not_select_credentials_on_a_join`
    scans a page's JOINs for password/token columns. It fetched
    `/passed_legislation/`, which now 404s — and **a 404 has no joins**, so the
    test passed by finding nothing to look at. Measured: 200 -> 2 joins,
    404 -> 0 joins.
  * `test_dev_mode_rows.test_no_ballot_content_reaches_the_page` asserts
    `assertNotIn` against the rendered body. A 404 body satisfies every
    absence assertion trivially.

Both are security guards, and both went quiet without failing. That is the
fourth time this codebase has been bitten by a check that appeared to run and
did not: the IP-blacklist sweep silently shrinking to 132 of 282 pages, the
attendance test passing against the very field definition it existed to catch,
the detail-route sweep rendering 83 of 319, and now this.

**The lesson these share: a test that cannot fail is worse than no test,
because it is trusted.** Where the check is "X is absent", assert first that
there was something to be absent from.

WHAT THIS TEST DOES
-------------------
Scans `src/`, `templates/` and `Parliament/` for string literals that look like
site paths, and fails on any that Django cannot resolve. `urls.py` is skipped
(it defines the routes) and so is the legacy-redirect block by extension.

The allowlist below is for strings that look like paths but are not: URL
*fragments* built by JS concatenation, filesystem paths, honeypot decoys,
middleware prefix comparisons, and settings-driven internal locations.
"""

import pathlib
import re

from django.test import SimpleTestCase
from django.urls import Resolver404, resolve

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: A quoted string starting with `/` and looking like a URL path.
#:
#: The trailing `(?:[?#][^'"]*)?` matters: the first version of this pattern
#: required the quote to follow the path directly, so it silently skipped every
#: literal carrying a query string — including
#: `link='/passed_legislation/?status=personal'` in `tasks/votes.py`, which is
#: the single most consequential one in the codebase (it is written into
#: `Notification.link` rows in production). A scanner that quietly declines to
#: look at a case is the same failure this whole module is about.
_PATH_RE = re.compile(
    r"""['"](/[a-zA-Z0-9][a-zA-Z0-9._~\-/]*/?)(?:[?#][^'"]*)?['"]""")

#: Prefixes served by something other than the URLconf.
_SKIP_PREFIXES = ('/static/', '/media/', '/admin/', '/api/', '/ws/', '/__',
                  '/internal_media')

#: Literals that look like site paths but are not. Each needs a reason — an
#: unexplained entry here is how a real broken link gets waved through.
_NOT_SITE_PATHS = {
    # URL *fragments* concatenated in JS: `base + '/approve/'`.
    '/approve/', '/reject/', '/revoke/', '/scopes/', '/notes/', '/notes/add/',
    '/delete/', '/delete/123/', '/update/', '/recruitment-permissions/',
    '/note/', '/0/', '/cnb/api/section/', '/legislation/', '/members/',
    '/signups/export/', '/admin-v2/api-tokens/flag/', '/passed-legislation/5/',
    # Filesystem paths, not URLs.
    '/var/backups/parliament/', '/src/dev_mode.py', '/src/middleware/dev_mode.py',
    # Middleware prefix comparisons against request.path — matched with
    # startswith, so they need not be routes themselves.
    '/health/', '/favicon.ico', '/lockdown/',
    '/accounts/two-factor/recovery-confirm/', '/accounts/passkeys/authenticate/',
    '/officers/db-dump/',        # deliberately dead — see geo_restriction.py
    # The 2FA exemption list compares prefixes; this one fronts a
    # `<path:filename>` route, so the bare prefix is not itself resolvable.
    '/exportable-media/',
    # v3.18.0: the UNDERSCORE spelling is in the same exemption list, and also
    # deliberately. v3.17.6 renamed the route and added a legacy redirect at
    # `exportable_media/<path:filename>` so old links keep working — but
    # Enforce2FAMiddleware runs BEFORE URL resolution, so an <img> on the old
    # path was 2FA-redirected instead of reaching its 302 (v3.17.7 finding 5).
    # Like its hyphenated twin, the bare prefix fronts a `<path:...>` route and
    # is not itself resolvable. Delete both this entry and the exemption when
    # the legacy redirect in src/urls.py goes.
    #
    # Worth recording: this test caught that line the moment it was written, and
    # v3.17.7 shipped without anyone running it — the same "a guard nobody ran"
    # shape the module itself is about.
    '/exportable_media/',
    # Honeypot decoys: the whole point is that they are not real routes.
    '/phpmyadmin',
    # `admin_v2.py` skips page-visit tracking for BOTH spellings of the admin
    # path on purpose — visits arriving at the legacy redirect should not be
    # tracked either. It is a startswith comparison, not a link.
    '/admin_v2/',
    # Test fixtures and doc examples.
    '/some-protected-page/', '/login', '/admin-v2', '/events/',
    '/constitution-bylaws/officer-duties/', '/constitution-bylaws/committees/',
}

_SCANNED_SUFFIXES = ('.py', '.html', '.js')

#: Django comment blocks. Stripped before scanning, because prose *about* a
#: broken path is not a broken path — the first run of this test flagged the
#: three `{% comment %}` blocks written in the same commit to explain the very
#: links being fixed.
_TEMPLATE_COMMENT_RE = re.compile(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}',
                                  re.S)


def _strip_comments(text, suffix):
    """Blank out comment blocks, preserving line numbering."""
    if suffix != '.html':
        return text

    def _blank(match):
        return '\n' * match.group(0).count('\n')

    text = _TEMPLATE_COMMENT_RE.sub(_blank, text)
    # `{# ... #}` is SINGLE-LINE in Django — a multi-line one is not a comment
    # and its contents really do render, so only the single-line form is
    # stripped here. Treating the multi-line form as a comment would reproduce
    # the bug CLAUDE.md records.
    return re.sub(r'\{#[^\n#]*#\}', '', text)


def _looks_like_a_template_expression(url):
    return any(ch in url for ch in '{}%<>$')


class HardcodedUrlPathsResolveTests(SimpleTestCase):

    def _scan(self):
        for directory in ('src', 'templates', 'Parliament'):
            for path in sorted((ROOT / directory).rglob('*')):
                if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
                    continue
                if path.name == 'urls.py':
                    continue
                text = _strip_comments(
                    path.read_text(encoding='utf-8', errors='ignore'), path.suffix)
                for line_no, line in enumerate(text.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith(('#', '//', '*')):
                        continue
                    for match in _PATH_RE.finditer(line):
                        url = match.group(1)
                        if (url in _NOT_SITE_PATHS
                                or url.startswith(_SKIP_PREFIXES)
                                or len(url) < 3
                                or _looks_like_a_template_expression(url)):
                            continue
                        yield path.relative_to(ROOT), line_no, url

    def test_every_hardcoded_path_resolves(self):
        offenders = []
        for rel_path, line_no, url in self._scan():
            try:
                resolve(url)
            except Resolver404:
                offenders.append(f'{rel_path}:{line_no}  {url}')
            except Exception:
                # A converter rejecting a sample value is not a dead route.
                continue

        self.assertEqual(
            offenders, [],
            'Hardcoded paths that do not resolve. A renamed route updates every '
            "`{% url %}` and `reverse()` automatically and leaves these behind — "
            'silently, because a dead link is a 404 and a 404 in a test is often '
            'indistinguishable from a pass. Use `reverse()` / `{% url %}`, or add '
            'the string to _NOT_SITE_PATHS with a reason if it is not a site path.',
        )

    def test_the_allowlist_has_no_dead_entries(self):
        """An exemption nobody needs makes the check look weaker than it is."""
        seen = set()
        for directory in ('src', 'templates', 'Parliament'):
            for path in sorted((ROOT / directory).rglob('*')):
                if not path.is_file() or path.suffix not in _SCANNED_SUFFIXES:
                    continue
                for match in _PATH_RE.finditer(_strip_comments(
                        path.read_text(encoding='utf-8', errors='ignore'),
                        path.suffix)):
                    seen.add(match.group(1))

        stale = sorted(_NOT_SITE_PATHS - seen)
        self.assertEqual(stale, [], 'allowlisted paths that appear nowhere')
