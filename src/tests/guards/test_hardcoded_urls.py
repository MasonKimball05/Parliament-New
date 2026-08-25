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

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent

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
    # Filesystem paths, not URLs. v3.21.5 moved the absolute ones onto
    # `_is_filesystem_path`; these two are repo-relative source references in
    # prose, which no rule about system directories can cover.
    '/src/dev_mode.py', '/src/middleware/dev_mode.py',
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
    # v3.19.7 — synthetic paths inside test fixtures. Both are strings a test
    # INVENTS rather than a route it visits, and neither can be written as a
    # `reverse()`:
    #   * `/slow/` labels a hand-built performance-metric tuple in
    #     `test_middleware_hot_path`; the assertion is that a slow entry is
    #     stored unconditionally, and the path is only there to be recognised.
    #   * `/test_` is a MODULE-path fragment in `test_client_ip_single_source`
    #     (`'/test_' in rel`), matched against filenames, not URLs. It is here
    #     rather than in `_SKIP_PREFIXES` because the scanner has no way to tell
    #     a filename fragment from a URL prefix by shape alone.
    #
    # ⚠️ The third and fourth offenders in this group were NOT exempted:
    # `/global-search/` in the same fixture was a genuine mistake (the route is
    # `/search/`) and was fixed. **The exemption list is for strings that are not
    # site paths — not for site paths that are wrong.** Adding a wrong path here
    # would have converted a caught bug into a documented one.
    '/slow/', '/test_',
    # Honeypot decoys: the whole point is that they are not real routes.
    '/phpmyadmin',
    # `admin_v2.py` skips page-visit tracking for BOTH spellings of the admin
    # path on purpose — visits arriving at the legacy redirect should not be
    # tracked either. It is a startswith comparison, not a link.
    '/admin_v2/',
    # Test fixtures and doc examples.
    '/some-protected-page/', '/login', '/admin-v2', '/events/',
    '/constitution-bylaws/officer-duties/', '/constitution-bylaws/committees/',
    # v3.19.8 — two more that are genuinely not site paths, and this guard was
    # right to stop and ask:
    #   * `/protected-media` (and the URI built beneath it) is an **nginx
    #     internal location**, the value of `MEDIA_ACCEL_PREFIX`. It is by
    #     definition not resolvable by Django — that is what `internal;` means
    #     in the nginx config — and `test_upload_serving_disposition` asserts
    #     the exact `X-Accel-Redirect` header value, so the string has to be
    #     written out rather than reversed.
    # Same reasoning as `/slow/` and `/test_` above, and the same caveat: these
    # are here because they are not site paths, not because they are site paths
    # that fail to resolve.
    #
    # ⚠️ v3.19.9 — `/main.xml` WAS ALSO EXEMPTED HERE AND IS NOT ANY MORE. It
    # was an OOXML **part name** in a synthesised document fixture, and by
    # 08-15-26 a second one had arrived (`/word/document.xml`). Two instances of
    # one mechanism is where a literal stops being honest: every test that
    # builds an Office document by hand writes part names, a part name is a
    # rooted path *by specification*, and the list would have grown one entry
    # per fixture forever. See `_is_ooxml_part_name` below — the rule is that a
    # path introduced by `PartName=` is a package part, not a URL, and it costs
    # nothing to state exactly.
    '/protected-media', '/protected-media/legislation_docs/notice.html',
}

#: `<Override PartName="/word/document.xml" ContentType="…"/>` — the part name
#: is rooted at the package, not at the site.
#:
#: ⚠️ THIS IS DELIBERATELY THE NARROWEST RULE THAT COVERS THE MECHANISM. The
#: tempting version is "ignore anything ending `.xml`", which would also ignore
#: a genuinely broken link to an `.xml` route, and "ignore paths in test files",
#: which would blind the scanner to exactly the fixtures that caught real bugs
#: in v3.19.7. `PartName=` appears nowhere in this codebase except inside an
#: OOXML content-type manifest, so the exemption cannot reach a real link — and
#: if that ever stops being true, `test_a_path_not_introduced_by_partname_is_
#: still_scanned` is the control that says so.
_PART_NAME_INTRODUCER_RE = re.compile(r'PartName\s*=\s*$')


def _is_ooxml_part_name(line, match):
    """True if this literal is introduced by an OOXML `PartName=` attribute."""
    return bool(_PART_NAME_INTRODUCER_RE.search(line[:match.start()]))

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


#: Filesystem roots that a URL router will never own.
#:
#: ⚠️ v3.21.5 — A RULE, AND THE LITERAL IT REPLACES IS DELETED. `/var/backups/
#: parliament/` used to sit in `_NOT_SITE_PATHS` under a comment reading
#: "Filesystem paths, not URLs" — a correct classification written one path at a
#: time, which is the shape v3.19.6 recorded as *a set is only the general form
#: if something enumerates the population it is drawn from*. The population here
#: is "absolute paths rooted at a Unix system directory", and it is a two-line
#: rule.
#:
#: The literal is removed rather than kept alongside, because an entry that also
#: matches the rule makes the rule untestable: it would keep passing if the rule
#: were wrong. `test_a_filesystem_path_is_not_treated_as_a_route` is the control.
#:
#: `/etc` and `/usr` are not listed — nothing in this codebase writes them, and a
#: root that never appears is an exemption that cannot be checked.
#:
#: ⚠️ THE TRAILING SLASHES ARE LOAD-BEARING AND THE CONTROL CAUGHT ME WITHOUT
#: THEM. The first draft was `('/tmp', '/var/')`, and `startswith('/tmp')` also
#: matches `/tmpl/`. That is the failure mode an exemption-by-rule has and an
#: exemption-by-literal does not: it can silently grow. The bare `/tmp` is
#: matched by equality instead, because it is a real directory that appears on
#: its own in a `LOG_DIR` value.
_FILESYSTEM_ROOTS = ('/tmp/', '/var/')  # nosec B108  # no file is opened here; these are prefixes a URL scanner must ignore


def _is_filesystem_path(url):
    """True for an absolute path under a Unix system directory, not a route."""
    return url == '/tmp' or url.startswith(_FILESYSTEM_ROOTS)  # nosec B108  # a string comparison, not a path this process writes to


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
                                or _is_ooxml_part_name(line, match)
                                or _is_filesystem_path(url)
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

    def test_a_filesystem_path_is_not_treated_as_a_route(self):
        """
        THE CONTROL FOR `_is_filesystem_path`, and the reason the
        `/var/backups/parliament/` literal could be deleted rather than left
        beside the rule.

        Both halves matter. If the rule matched too little, the literal it
        replaced would be needed again; if it matched too much — a prefix test
        against a system directory name without its trailing separator also
        swallows a route that merely begins with the same letters — it would
        silence real broken links without appearing in `_NOT_SITE_PATHS` where
        somebody could read it. So the separators are part of the rule, and
        that is asserted below rather than trusted.

        ⚠️ AND THE PROSE ABOVE IS DELIBERATELY SEPARATOR-FREE. The first draft
        of this docstring named the two roots, and this module scans this
        module: a Python docstring is not a comment line, so `_scan` reads it
        and duly reported the explanation as a broken link. Fourth time — the
        module's own history records the `{% comment %}` blocks, the worked
        example above `_PART_NAME_INTRODUCER_RE`, and a control fixture.
        """
        # ⚠️ ASSEMBLED, NOT WRITTEN — for the same reason as the `PartName`
        # control below. A rooted literal in this file is a rooted literal in
        # the tree this file scans, and `/tmpl/` and `/variance/` are not
        # routes, so writing them out would make the scanner report its own
        # control fixture. Third time this module has done this to itself.
        root = '/'
        for path in (root + 'tmp', root + 'tmp/last_digest_sent',
                     root + 'var/backups/parliament/'):
            with self.subTest(path=path):
                self.assertTrue(_is_filesystem_path(path))

        for path in (root + 'variance/', root + 'tmpl/', root + 'vote/'):
            with self.subTest(path=path):
                self.assertFalse(
                    _is_filesystem_path(path),
                    'a site route that merely starts with the same letters must '
                    'still be scanned — the first draft of this rule used '
                    'startswith on a bare /tmp and swallowed one of these',
                )

    def test_a_path_not_introduced_by_partname_is_still_scanned(self):
        """
        THE CONTROL FOR `_is_ooxml_part_name`, and it is the whole reason that
        rule is safe to prefer over two literals.

        An exemption expressed as a rule is a claim about a mechanism, and a
        rule that turned out to match more than the mechanism would silence real
        broken links without ever appearing in `_NOT_SITE_PATHS` for someone to
        read. So: the identical path is exempt with the introducer and scanned
        without it.
        """
        # ⚠️ ASSEMBLED, NOT WRITTEN. A rooted path literal in this file is a
        # rooted path literal in the tree this file scans, and the first draft
        # duly reported its own fixture as an unresolvable link. Splitting after
        # the leading slash defeats `_PATH_RE` (the `'/'` half is under the
        # three-character floor and the other half is not rooted) without
        # defeating the thing under test, which reads a whole line.
        path = '/' + 'word/document.xml'
        with_introducer = f'    manifest = \'<Override PartName="{path}"/>\''
        without_introducer = f'    link = "{path}"'

        exempted = [
            m for m in _PATH_RE.finditer(with_introducer)
            if not _is_ooxml_part_name(with_introducer, m)
        ]
        self.assertEqual(exempted, [], 'a PartName= part should be exempt')

        scanned = [
            m.group(1) for m in _PATH_RE.finditer(without_introducer)
            if not _is_ooxml_part_name(without_introducer, m)
        ]
        self.assertEqual(
            scanned, [path],
            'the same string without the PartName= introducer must still be '
            'scanned — otherwise the rule is exempting by shape, not by '
            'mechanism',
        )

    def test_every_path_this_rule_exempts_is_in_an_ooxml_manifest(self):
        """
        The rule's safety rests on `PartName=` being unambiguous in this
        codebase. That is a fact about the tree, not about the format, so it is
        asserted rather than assumed.

        ⚠️ TWO DRAFTS OF THIS TEST FLAGGED THIS MODULE'S OWN PROSE. The first
        grepped for the substring `PartName` and reported ten lines of comment
        and docstring here that quote it while explaining the rule. The second
        narrowed to paths actually exempted — and still reported one, because
        the doc comment above `_PART_NAME_INTRODUCER_RE` shows a worked
        *example* of the attribute, which is indistinguishable from the real
        thing by construction.

        So it skips comment lines, exactly as `_scan` does, for exactly the
        reason `_strip_comments` exists: **writing about a thing is not doing
        it**, and a scanner run over the file that documents it will always find
        the documentation first.
        """
        offenders = []
        for directory in ('src', 'templates', 'Parliament'):
            for file_path in sorted((ROOT / directory).rglob('*')):
                if not file_path.is_file() or file_path.suffix not in _SCANNED_SUFFIXES:
                    continue
                text = _strip_comments(
                    file_path.read_text(encoding='utf-8', errors='ignore'),
                    file_path.suffix)
                for line_no, line in enumerate(text.splitlines(), 1):
                    if line.strip().startswith(('#', '//', '*')):
                        continue
                    for match in _PATH_RE.finditer(line):
                        if not _is_ooxml_part_name(line, match):
                            continue
                        if 'openxmlformats' not in line:
                            offenders.append(
                                f'{file_path.relative_to(ROOT)}:{line_no}  '
                                f'{match.group(1)}')

        self.assertEqual(
            offenders, [],
            'a path exempted as an OOXML part name, on a line that names no '
            'OOXML namespace. `_is_ooxml_part_name` silences whatever it '
            'matches, so this is the boundary of that exemption and it has '
            'moved.',
        )
