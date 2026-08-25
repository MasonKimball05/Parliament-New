"""
Multi-line `{# … #}` template comments render. This makes that fail at test time.

⚠️ THIS IS THE THIRD OCCURRENCE, WHICH IS WHY IT IS NOW A TEST.

Django's `{# … #}` comment syntax is **single-line only**. The lexer's comment
pattern does not span newlines, so a comment written across several lines is not
recognised as a comment at all: the opening `{#` is consumed, and the remaining
lines are emitted as template text — visible to the reader, in the middle of the
page, in whatever font the surrounding element uses.

The history in this repo:

  * **07-30-26** (`5965773`, commit message: *"Forgot multi line comments just
    show on the template lol"*). Recorded in CLAUDE.md under v3.16.3 as a rule
    to remember.
  * **08-06-26** — v3.19.0 shipped two on the My Work page, one of which Mason
    spotted rendering above the Drafts tab.
  * **08-06-26**, same day — v3.19.1 added two more to `cnb/viewer.html`, written
    by someone who had read the rule earlier in the same session.

That last one is the argument for this file. The rule was known, written down,
and recently paid for, and it was broken anyway within hours — because nothing
enforces it, the failure is invisible in review (a comment block *looks* like a
comment block), and it only shows up if someone happens to load the page and
read carefully. **A rule that lives only in prose gets followed until the moment
attention is elsewhere.** Same lesson CLAUDE.md draws about checks becoming
rituals, pointing the other way: this one never became a check at all.

`{% comment %} … {% endcomment %}` is the multi-line form and has no such limit.

Pure file scanning — no DB, no rendering — so it runs under SimpleTestCase.
"""
import re

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATE_DIR = settings.BASE_DIR / 'templates'

# The Django admin's own templates are vendored; not ours to police.
EXEMPT_PREFIXES = ('admin/',)

COMMENT_OPEN_RE = re.compile(r'\{#')


def _template_files():
    for path in sorted(TEMPLATE_DIR.rglob('*.html')):
        rel = str(path.relative_to(TEMPLATE_DIR))
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        yield rel, path.read_text(errors='replace')


def find_multiline_comments(text):
    """
    `[(line_number, span_in_lines, excerpt), …]` for every `{#` whose closing
    `#}` is not on the same line — plus any `{#` that is never closed at all.

    Deliberately scans the RAW TEXT rather than using Django's lexer. Asking the
    lexer would mean asking the component whose behaviour is the bug: it does
    not consider these comments, so it reports nothing wrong. The question here
    is "did a human intend a comment", and the honest way to answer it is to
    look at what the human wrote.
    """
    findings = []
    for match in COMMENT_OPEN_RE.finditer(text):
        start = match.start()
        line_no = text.count('\n', 0, start) + 1
        close = text.find('#}', start)

        if close == -1:
            findings.append((line_no, None, text[start:start + 90]))
            continue

        body = text[start:close]
        if '\n' in body:
            findings.append((line_no, body.count('\n') + 1, body[:90]))
    return findings


class TemplateCommentSyntaxTests(SimpleTestCase):

    def test_no_multiline_hash_comments(self):
        """
        A `{# … #}` spanning more than one line is not a comment — it is text on
        the page. Use `{% comment %} … {% endcomment %}`.
        """
        offenders = []
        for rel, text in _template_files():
            for line_no, span, excerpt in find_multiline_comments(text):
                if span is None:
                    offenders.append(
                        f'  {rel}:{line_no}  UNCLOSED `{{#` — everything after it '
                        f'is being rendered\n      {excerpt.strip()[:80]}…'
                    )
                else:
                    offenders.append(
                        f'  {rel}:{line_no}  spans {span} lines\n'
                        f'      {excerpt.strip()[:80]}…'
                    )

        self.assertEqual(
            offenders, [],
            'Multi-line `{# … #}` comments found. Django\'s hash-comment syntax is '
            'SINGLE-LINE ONLY — these are not comments, their text renders on the '
            'page. Convert each to `{% comment %} … {% endcomment %}`:\n\n'
            + '\n'.join(offenders),
        )

    def test_the_detector_recognises_a_bad_comment(self):
        """
        ⚠️ THE NEGATIVE CONTROL, and this file needs one more than most.

        `test_no_multiline_hash_comments` passes when it finds nothing — which
        is also what a detector that can find nothing does. Since the whole
        point of this module is that the bug is invisible, a silently broken
        detector would restore exactly the situation it was written to end.

        Asserted against a literal rather than a fixture file so it cannot be
        fixed by someone tidying up templates.
        """
        bad = '<div>\n  {# this comment\n     runs over two lines #}\n</div>'
        self.assertEqual(len(find_multiline_comments(bad)), 1)

        unclosed = '<div>\n  {# someone forgot to close this\n</div>'
        found = find_multiline_comments(unclosed)
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0][1], 'An unclosed `{#` should report no span.')

    def test_the_detector_accepts_good_comments(self):
        """The other half of the control: single-line comments must not trip it."""
        good = (
            '<div>\n'
            '  {# a perfectly ordinary single-line comment #}\n'
            '  {% comment %}\n'
            '  a multi-line comment written the correct way,\n'
            '  which must not be flagged\n'
            '  {% endcomment %}\n'
            '  {# another one #}{# and one on the same line #}\n'
            '</div>'
        )
        self.assertEqual(find_multiline_comments(good), [])
