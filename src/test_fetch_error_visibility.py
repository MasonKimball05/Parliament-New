"""
v3.20.1 — a failed request has to be visible to the person who made it.

⚠️ THE BUG THIS EXISTS FOR. Every interactive control on the education
dashboard had been answering 403 since the page was written, and nobody could
tell: each handler caught the rejection and only wrote it to the console. The
page did nothing at all — no message, no log the chapter reads, nothing in
Sentry. The CSP-violations page had the same shape on its Dismiss buttons.

**A silent failure is worse than a crash.** A crash gets reported the first day
somebody hits it; this hid for months and was found only because Mason happened
to try a button and think to mention it.

So `base.html` now defines `Parliament.post()`, which does two things that
between them cover the whole bug:

1. **One place knows where the CSRF token comes from.** Three templates had each
   written their own read, two of them against a cookie that
   `CSRF_COOKIE_HTTPONLY = True` makes unreadable. A helper cannot be copied
   wrong.
2. **A non-2xx raises and is shown to the user**, unless the caller passes
   `quiet: true` because it is rendering its own error.

⚠️ THIS IS A RATCHET, NOT A CLEAN BILL OF HEALTH. There are 39 templates
sending a hand-written `X-CSRFToken` and, when this was written, **twelve**
handlers that still swallow a failure into the console. Converting all of them
in one change would be a large blind edit of code nobody has a test for. So the
count is pinned at what was measured, the offenders are listed, and the number
may only go **down**. The two pages known to be broken are converted; the rest
get converted as they are touched.

To re-measure after converting one::

    python3 - <<'PY'
    import re, pathlib
    for p in sorted(pathlib.Path('templates').rglob('*.html')):
        t = p.read_text(errors='ignore')
        ...  # same scan as _swallowing_handlers below
    PY
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Measured 08-19-26, after converting `committee/education.html` (6 handlers)
#: and `admin_v2/csp_violations.html` (1). **Lower this when you convert one.**
#: It must never rise: a new silently-swallowed failure is the defect this
#: module exists to stop.
KNOWN_SWALLOWING_HANDLERS = 12

#: Templates converted to `Parliament.post`, which must stay converted. These
#: are the two that were provably broken in production.
CONVERTED = (
    'committee/education.html',
    'admin_v2/csp_violations.html',
)


def _blank_comments(text):
    """
    Replace comment bodies with newlines, preserving line numbers.

    ⚠️ REQUIRED, NOT TIDINESS. The first run of this scan flagged
    `base.html` — because the comment written to *explain* the pattern quoted
    it. Same lesson as `test_nosec_hygiene` and `test_csrf_token_source`:
    a scanner run over the tree that documents it finds the documentation
    first.
    """
    def blank(match):
        return '\n' * match.group(0).count('\n')

    text = re.sub(r'<!--.*?-->', blank, text, flags=re.DOTALL)
    text = re.sub(r'/\*.*?\*/', blank, text, flags=re.DOTALL)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.MULTILINE)
    return text


def _templates():
    root = Path(settings.BASE_DIR) / 'templates'
    return sorted(root.rglob('*.html'))


def _swallowing_handlers():
    """
    `(path:line)` for every catch block whose entire body is console calls.

    Deliberately narrow: a catch that restores UI state, re-raises, or shows
    anything is not a defect. Only "log it and carry on as if nothing happened"
    is.
    """
    found = []
    pattern = re.compile(r'catch\s*\([^)]*\)\s*\{([^{}]*)\}')
    console_only = re.compile(r'^console\.\w+\(.*\);?$')

    for path in _templates():
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        stripped = _blank_comments(text)
        for match in pattern.finditer(stripped):
            lines = [ln.strip() for ln in match.group(1).split('\n') if ln.strip()]
            if lines and all(console_only.match(ln) for ln in lines):
                line_no = stripped[:match.start()].count('\n') + 1
                found.append(f'{path.relative_to(settings.BASE_DIR)}:{line_no}')
    return found


class TheHelperExistsTests(SimpleTestCase):
    """The control. Everything below points at this."""

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_base_defines_the_post_helper(self):
        self.assertIn('P.post = function', self.base)

    def test_base_defines_the_toast(self):
        self.assertIn('P.toast = function', self.base)

    def test_the_helper_is_defined_before_page_content_renders(self):
        """
        ⚠️ Page scripts live inside `{% block content %}`, which base.html
        renders *before* its own later script blocks — so a helper defined
        further down the file would not exist when a page tried to call it, and
        the failure would be a console error on a page nobody is watching.
        Which is the bug this module is about.
        """
        # ⚠️ Indexed into the COMMENT-STRIPPED source. The first draft used
        # the raw file and failed, because the helper's own docstring explains
        # *why* it must precede the content block — and therefore names it.
        # Fourth self-reference of the day; the pattern is now expected.
        stripped = _blank_comments(self.base)
        helper_at = stripped.index('P.post = function')
        content_at = stripped.index('{% block content %}')
        self.assertLess(
            helper_at, content_at,
            'Parliament.post is defined after {% block content %}. Page scripts '
            'run first and will see it undefined.',
        )

    def test_the_helper_sends_the_csrf_token_from_the_meta_tag(self):
        self.assertIn("meta[name=\"csrf-token\"]", self.base)
        self.assertIn("'X-CSRFToken': csrfToken()", self.base)


class ConvertedPagesStayConvertedTests(SimpleTestCase):
    """
    These two were provably broken in production. A regression here is not a
    style question — it is the same 403 coming back.
    """

    def _read(self, name):
        return (Path(settings.BASE_DIR) / 'templates' / name).read_text(encoding='utf-8')

    def test_they_use_the_helper(self):
        for name in CONVERTED:
            with self.subTest(template=name):
                self.assertIn('Parliament.post(', self._read(name))

    def test_they_do_not_hand_write_the_csrf_header(self):
        for name in CONVERTED:
            with self.subTest(template=name):
                body = _blank_comments(self._read(name))
                self.assertNotIn(
                    'X-CSRFToken', body,
                    f'{name} builds the CSRF header by hand again. That is the '
                    f'exact line that was wrong for months — let the helper do it.',
                )

    def test_they_contain_no_bare_fetch(self):
        for name in CONVERTED:
            with self.subTest(template=name):
                body = _blank_comments(self._read(name))
                self.assertNotIn('fetch(', body)


class SilentFailuresDoNotGrowTests(SimpleTestCase):
    """The ratchet."""

    def test_the_scan_is_not_vacuous(self):
        self.assertGreater(len(_templates()), 50)

    def test_no_new_silently_swallowed_request_failures(self):
        offenders = _swallowing_handlers()
        self.assertLessEqual(
            len(offenders), KNOWN_SWALLOWING_HANDLERS,
            f'{len(offenders)} handlers swallow a failure into the console, up '
            f'from {KNOWN_SWALLOWING_HANDLERS}:\n  ' + '\n  '.join(offenders)
            + '\n\nUse Parliament.post() — it reports the failure for you. If '
              'the caller really must stay silent, pass {quiet: true} and say '
              'why in a comment.',
        )

    def test_the_ratchet_is_tight(self):
        """
        ⚠️ A ceiling nobody lowers stops constraining anything — the same rule
        `test_query_budgets.py` applies to queries. If this fails, someone
        converted a page and did not lower the constant: do that, in the same
        commit that earned it.
        """
        offenders = _swallowing_handlers()
        self.assertGreaterEqual(
            len(offenders), KNOWN_SWALLOWING_HANDLERS,
            f'Only {len(offenders)} swallowing handlers remain but the constant '
            f'still says {KNOWN_SWALLOWING_HANDLERS}. Lower '
            f'KNOWN_SWALLOWING_HANDLERS to {len(offenders)}.',
        )
