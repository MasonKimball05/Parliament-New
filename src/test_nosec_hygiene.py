"""
v3.19.11 — the shape of every bandit suppression in this project.

⚠️ WHY THIS EXISTS. The 08-19-26 report proposed a tidy pass over these
comments: convert the space-separated `B308 B703 - reason` form into the
documented comma form. **Running it first showed that the tidy would have
reddened CI**, which is the whole reason this file is a test and not a style
note.

Measured 08-19-26 against bandit 1.9.4 with a minimal probe:

    mark_safe(...)   with the ids NAMED   →  B308 still REPORTED
    mark_safe(...)   with a BARE directive →  suppressed
    f-string SQL     with the ids NAMED   →  suppressed

So on a `mark_safe` line, naming the ids does not suppress B308 — the pair form
gets you half a suppression and a red gate. v3.19.10 discovered this for
`dev_tags.py` and wrote it down in place; it is true of all five `mark_safe`
sites, and the only reason the other four were green is that their prose
justification contained invalid tokens, which bandit discards, which
**accidentally** promoted them to blanket suppressions. A codebase relying on
that is one cleanup away from a red build.

The four properties below are therefore:

1. every directive parses cleanly — no prose inside the part bandit reads as a
   list of test ids;
2. every directive carries a justification, because the CI file asks for
   *justified* suppressions and an unexplained one cannot be reviewed;
3. no directive sits on a comment-only line;
4. `mark_safe` sites use the bare form, for the measured reason above.

**Property 3 is a real bug this release fixed, not a hypothetical.** A comment
block in `dev_tags.py` wrapped so that a line *began* with the directive
spelling, and bandit read it as a directive: the sentence written to explain a
suppression became a second, blanket suppression, on a comment line, covering
whatever code a later edit might move onto it. It also produced about eighteen
"not a test name" warnings out of its own prose. The first draft of the note
explaining *that* reintroduced it, by quoting the spelling in backticks.

⚠️ **WHICH IS ALSO WHY THIS MODULE NEVER WRITES THE DIRECTIVE LITERALLY.** It
assembles the marker from two pieces at import time, so the file can be scanned
by its own rules with no exemption — the alternative is an exclusion list, and
an exclusion list is the thing that lets the next file quietly opt out.
CLAUDE.md already records the general form: *a scanner run over the tree
containing the file that documents it finds the documentation first, at every
level, including the one you just added.*
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

#: Assembled, never written literally — see the module docstring.
MARKER = '#' + ' nosec'

#: Mirrors bandit 1.9.4's own comment regex closely enough for these
#: assertions: everything after the directive up to the next `#` is what bandit
#: tries to read as a comma/whitespace separated list of test ids. Putting the
#: justification behind a SECOND `#` is what keeps prose out of that group.
_DIRECTIVE = re.compile(r'#\s*nosec:?\s*(?P<tests>[^#]*)(?P<rest>#.*)?$')

_TEST_ID = re.compile(r'^B\d+$')


def _source_files():
    root = Path(settings.BASE_DIR) / 'src'
    return sorted(
        path for path in root.rglob('*.py')
        if 'migrations' not in path.parts
    )


def _directives():
    """`(path, lineno, code_before, tests_group, justification)` for each one."""
    found = []
    for path in _source_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        if MARKER not in text:
            continue
        for lineno, line in enumerate(text.split('\n'), start=1):
            index = line.find(MARKER)
            if index == -1:
                continue
            match = _DIRECTIVE.search(line, index)
            if not match:
                continue
            found.append((
                path.relative_to(settings.BASE_DIR),
                lineno,
                line[:index],
                (match.group('tests') or '').strip(),
                (match.group('rest') or '').lstrip('#').strip(),
            ))
    return found


class EveryBanditSuppressionIsWellFormedTests(SimpleTestCase):

    def test_the_scan_is_not_vacuous(self):
        """
        A scan that matches nothing passes every assertion below. This project
        has had a suppression on essentially every release since 07-29-26, so
        the real number is well into double figures; the threshold is loose on
        purpose — it is a smoke alarm for a broken scan, not a ratchet on how
        many suppressions are acceptable.
        """
        self.assertGreaterEqual(len(_directives()), 10)

    def test_no_prose_leaks_into_the_part_bandit_reads_as_test_ids(self):
        """
        A directive followed by `B308 B703 - because reasons` makes bandit warn
        once per prose word, and — worse — the invalid tokens silently widen the
        suppression to a blanket one. Put the justification behind a second `#`.

        (Note this docstring does not spell the directive either. The first
        draft did, and this very test caught it — which is the third time in
        one release that writing the pattern down created an instance of it.)
        """
        offenders = []
        for path, lineno, _code, tests, _why in _directives():
            tokens = [t for t in re.split(r'[,\s]+', tests) if t and t != '-']
            bad = [t for t in tokens if not _TEST_ID.match(t)]
            if bad:
                offenders.append(f'{path}:{lineno} — stray {bad}')
        self.assertEqual(
            offenders, [],
            'A justification must go behind a SECOND "#", not inside the test '
            'id list:\n  ' + '\n  '.join(offenders),
        )

    def test_every_suppression_says_why(self):
        """
        `.github/workflows/ci.yml` asks for an inline justification rather than
        a gate downgrade. An unexplained suppression cannot be reviewed, and the
        review is the only thing standing behind it.
        """
        offenders = [
            f'{path}:{lineno}'
            for path, lineno, _code, _tests, why in _directives()
            if not why
        ]
        self.assertEqual(
            offenders, [],
            'These suppressions carry no justification:\n  '
            + '\n  '.join(offenders),
        )

    def test_no_suppression_sits_on_a_comment_only_line(self):
        """
        ⚠️ REGRESSION TEST — this happened, in `dev_tags.py`, to the comment
        written to explain a suppression. bandit's pattern matches inside a
        comment as readily as after code, so prose that spells the directive
        BECOMES one: a blanket suppression on a line with no code, which will
        silently cover whatever code a later edit moves onto it.

        Describe it ("the directive"); never spell it in prose.
        """
        offenders = [
            f'{path}:{lineno}'
            for path, lineno, code, _tests, _why in _directives()
            if not code.strip() or code.strip().startswith('#')
        ]
        self.assertEqual(
            offenders, [],
            'A suppression on a comment-only line suppresses nothing today and '
            'anything tomorrow:\n  ' + '\n  '.join(offenders),
        )

    def test_mark_safe_sites_use_the_bare_form(self):
        """
        ⚠️ THE COUNTERINTUITIVE ONE, AND THE REASON THIS FILE IS A TEST.

        Naming the ids is normally better — a narrow suppression cannot hide a
        second, unrelated finding on the same line. On a `mark_safe` line in
        bandit 1.9.4 it does not work: B703 is suppressed and **B308 is still
        reported**, so the gate goes red while the comment claims otherwise.

        Verified by probe rather than inherited: put a `mark_safe` call under
        each form and run `bandit -ll -f json` over it. Re-measure before
        changing this — if a later bandit fixes the pair form, the right move is
        to name the ids everywhere and delete this test, not to keep the blanket
        form because a test says so.
        """
        offenders = [
            f'{path}:{lineno} — names {tests!r}'
            for path, lineno, code, tests, _why in _directives()
            if 'mark_safe' in code and tests
        ]
        self.assertEqual(
            offenders, [],
            'Naming the ids on a mark_safe line leaves B308 reported and turns '
            'CI red. Use the bare form and name the ids in the justification:\n'
            '  ' + '\n  '.join(offenders),
        )
