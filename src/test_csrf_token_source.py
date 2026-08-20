"""
v3.20.0 — where JavaScript is allowed to get the CSRF token from.

⚠️ THE BUG THIS EXISTS FOR. `CSRF_COOKIE_HTTPONLY = True` (settings.py:285),
so **JavaScript can never read the `csrftoken` cookie**. Three templates read it
from `document.cookie` anyway:

* `committee/education.html` — `const CSRF = document.cookie.match(…) || ''`,
  so every fetch on the education dashboard sent `X-CSRFToken: ''` and Django
  answered 403. Delete Task, the completion grid, the publish toggle and both
  page-restriction endpoints **have never worked from the browser.** Each
  handler catches and `console.log`s, so the page silently did nothing — which
  is why it went unreported until someone tried the Delete button.
* `admin_v2/csp_violations.html` — same shape, same empty fallback. The Dismiss
  buttons have never worked.
* `preferences.html` — worked, but only by accident: it fell through to a
  rendered `{{ csrf_token }}`, while its comment claimed the cookie was
  preferred "so the token stays fresh". A false comment of exactly the kind
  CLAUDE.md records — true of a simpler deployment, never revisited.

`base.html` solved this properly long ago: it emits
`<meta name="csrf-token" content="{{ csrf_token }}">` and its `getCookie()`
prefers the meta tag, carrying the comment *"works even with
CSRF_COOKIE_HTTPONLY=True"*.

**So the helper existed, was correct, and was documented — and three templates
were outside it.** That is the shape this codebase has recorded eight releases
running, and the response it has settled on is not to fix the instances but to
enumerate the population. Hence this file: it walks every template and fails on
any new one that reaches for the cookie.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Assembled rather than written literally so this module does not match its own
#: prose — the same technique `test_nosec_hygiene.py` uses, and for the same
#: reason. A scanner run over the tree containing the file that documents it
#: finds the documentation first.
_COOKIE_READ = re.compile(r'document\s*\.\s*cookie[^\n]*' + 'csrf' + 'token')

#: Templates may still reference the cookie *name* in prose or in a comment
#: explaining why they do not use it; only a read is a defect.
_TEMPLATE_ROOT_NAMES = ('templates',)


def _template_files():
    base = Path(settings.BASE_DIR)
    for root_name in _TEMPLATE_ROOT_NAMES:
        root = base / root_name
        if not root.exists():
            continue
        yield from sorted(root.rglob('*.html'))


def _strip_comments(text):
    """
    Drop HTML, Django and JS line comments before scanning.

    Without this the scan flags the very comments written to explain the fix —
    `education.html` now contains a paragraph describing the cookie read it
    replaced. `test_hardcoded_urls` learned the same lesson and for the same
    reason.

    ⚠️ Line numbers are PRESERVED — each stripped span is replaced by the same
    number of newlines. The first draft deleted them outright, which shifted
    every subsequent line and made the failure message point at an innocent
    line 1892 of `base.html` while the real read was at 1960. A guard that
    reports the wrong location costs more than it saves.
    """
    def blank(match):
        return '\n' * match.group(0).count('\n')

    text = re.sub(r'<!--.*?-->', blank, text, flags=re.DOTALL)
    text = re.sub(r'\{#.*?#\}', blank, text, flags=re.DOTALL)
    text = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', blank, text, flags=re.DOTALL)
    text = re.sub(r'/\*.*?\*/', blank, text, flags=re.DOTALL)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.MULTILINE)
    return text


class JavaScriptNeverReadsTheCsrfCookieTests(SimpleTestCase):

    def test_the_cookie_is_httponly_which_is_why_this_rule_exists(self):
        """
        The premise. If this ever flips to False the rule below becomes merely a
        preference rather than a correctness requirement — and whoever flips it
        should see this test and decide deliberately.
        """
        self.assertTrue(
            settings.CSRF_COOKIE_HTTPONLY,
            'CSRF_COOKIE_HTTPONLY is now False. JavaScript can read the cookie '
            'again, so the scan below is no longer load-bearing — but the meta '
            'tag is still the better source. Decide, do not just delete.',
        )

    def test_the_scan_sees_a_realistic_number_of_templates(self):
        """A scan that matches nothing passes everything below vacuously."""
        self.assertGreater(len(list(_template_files())), 50)

    def test_no_template_reads_the_csrf_token_from_document_cookie(self):
        offenders = []
        for path in _template_files():
            try:
                text = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(_strip_comments(text).split('\n'), start=1):
                if _COOKIE_READ.search(line):
                    offenders.append(f'{path.relative_to(settings.BASE_DIR)}:{lineno}')

        self.assertEqual(
            offenders, [],
            'These templates read the CSRF token from document.cookie, which '
            'CSRF_COOKIE_HTTPONLY=True makes impossible — every request they '
            'send will be a 403:\n  ' + '\n  '.join(offenders)
            + '\n\nUse the meta tag base.html already renders:\n'
              "  document.querySelector('meta[name=\"csrf-token\"]')"
              ".getAttribute('content')",
        )

    def test_base_html_still_renders_the_meta_tag_everything_depends_on(self):
        """
        The control. Every fix above points at this tag; if it is ever removed
        from `base.html`, the templates that now depend on it fail closed and
        this test says why.
        """
        base = Path(settings.BASE_DIR) / 'templates' / 'base.html'
        self.assertIn('name="csrf-token"', base.read_text(encoding='utf-8'))
