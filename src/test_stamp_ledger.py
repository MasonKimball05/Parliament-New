"""
`make stamp-ledger` clears the gate it is advertised to clear.

⚠️ WHY THIS FILE EXISTS, AND IT IS NOT A HYPOTHETICAL.

`src.W003` reports two different problems: a changelog whose
`**Committed & pushed:**` line still says "not yet", and a committed release
with **no `DEPLOYED.md` row at all**. `scripts/stamp_ledger.py` is what the
pre-push hook tells you to run — *"Fix it with one command"* — and until
08-23-26 it only ever did the first of those two. It rewrote cells in rows that
already existed and could not create one.

So on the v3.25.0 push the hook refused, the tool ran, reported four lines
updated, said "now commit them", and the **next push was refused again** for the
half it had never touched. That is the eleventh blocked push this script was
written to prevent.

> **A tool that resolves one of the two things a check reports has moved the
> failure, not fixed it.** Same shape as v3.21.7's `IntegrityError` replacing a
> `ValidationError` — and worse here, because the hook's own message promises
> one command will do it.

And the reason nobody noticed: **`stamp_ledger.py` had no tests.** The script
whose entire job is to satisfy a gate was never itself checked against that
gate. `src/test_ledger_check.py` covers the check thoroughly and stops at the
edge of the tool.

⚠️ THE DEPLOYED COLUMN. A new row is written `*not deployed*`, and that is not
the script breaking its own rule about never inferring deployment. It is the
absence of a claim — the same words a person writes by hand — where an inferred
date would be a claim. `test_a_new_row_never_claims_a_deploy` pins it.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

from django.test import SimpleTestCase


def _load_script():
    """Import `scripts/stamp_ledger.py`, which is a script rather than a module."""
    path = Path(__file__).resolve().parent.parent / 'scripts' / 'stamp_ledger.py'
    spec = importlib.util.spec_from_file_location('stamp_ledger_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER_HEADER = (
    '# Deployment ledger\n'
    '\n'
    '| Release | Deployed | Commit | Notes |\n'
    '|---|---|---|---|\n'
)


class StampLedgerCreatesMissingRowsTests(SimpleTestCase):
    """
    Exercised against a temporary ledger, not the real one — the script keys off
    module-level paths, so the tests repoint those rather than the repository.
    """

    def setUp(self):
        self.script = _load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / 'DEPLOYED.md'
        self.script.LEDGER = self.ledger

    def _write(self, *rows):
        self.ledger.write_text(LEDGER_HEADER + ''.join(rows), encoding='utf-8')

    def _rows(self):
        """`{'v3.13.0': (deployed_cell, commit_cell)}` from the file on disk."""
        found = {}
        for line in self.ledger.read_text(encoding='utf-8').split('\n'):
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if cells and re.match(r'^v\d+\.\d+\.\d+$', cells[0]):
                found[cells[0]] = (cells[1], cells[2])
        return found

    def test_a_committed_release_with_no_row_gets_one(self):
        """⚠️ THE BUG. This is what the tool could not do."""
        self._write('| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n')

        changed = self.script.stamp_ledger_rows(
            {'v3.13.0': ('7e35a15', '07-15-26'),
             'v3.25.0': ('ea0dd36', '08-23-26')},
            dry_run=False,
        )

        self.assertIn('v3.25.0', self._rows())
        self.assertEqual(self._rows()['v3.25.0'][1], '`ea0dd36`')
        self.assertTrue(any('v3.25.0' in line for line in changed), changed)

    def test_a_new_row_never_claims_a_deploy(self):
        """
        ⚠️ THE RULE THE SCRIPT OPENS WITH, and the one line of it that a
        row-creating tool could plausibly break. Deployment is a fact about a
        server. `*not deployed*` is the absence of a claim; a date would be a
        claim, and the 07-23 → 07-31 eight-report error came from exactly a
        plausible-looking unverified one.
        """
        self._write('| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n')

        self.script.stamp_ledger_rows(
            {'v3.25.0': ('ea0dd36', '08-23-26')}, dry_run=False)

        self.assertEqual(self._rows()['v3.25.0'][0], '*not deployed*')

    def test_it_does_not_backfill_releases_older_than_the_ledger(self):
        """
        ⚠️ THE SCOPING CONTROL. `DEPLOYED.md` was reconstructed on 07-31-26 and
        starts at v3.13.0; seventy-odd older changelogs are legitimately absent.
        `src/checks_ledger.py::_ledger_begins_at` exists because the check's
        first run reported all of them, and a tool that *wrote* all of them
        would be the same mistake with a worse blast radius.
        """
        self._write('| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n')

        self.script.stamp_ledger_rows(
            {'v2.6.0': ('0000001', '01-01-26'),
             'v3.25.0': ('ea0dd36', '08-23-26')},
            dry_run=False,
        )

        rows = self._rows()
        self.assertIn('v3.25.0', rows)
        self.assertNotIn('v2.6.0', rows)

    def test_running_it_twice_does_not_duplicate_a_row(self):
        self._write('| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n')
        pending = {'v3.25.0': ('ea0dd36', '08-23-26')}

        self.script.stamp_ledger_rows(pending, dry_run=False)
        second = self.script.stamp_ledger_rows(pending, dry_run=False)

        body = self.ledger.read_text(encoding='utf-8')
        self.assertEqual(body.count('| v3.25.0 |'), 1)
        self.assertEqual(second, [])

    def test_a_dry_run_changes_nothing(self):
        self._write('| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n')
        before = self.ledger.read_text(encoding='utf-8')

        changed = self.script.stamp_ledger_rows(
            {'v3.25.0': ('ea0dd36', '08-23-26')}, dry_run=True)

        self.assertTrue(changed)
        self.assertEqual(self.ledger.read_text(encoding='utf-8'), before)

    def test_an_existing_deployed_date_is_never_overwritten(self):
        """
        The other half of the Deployed rule: the column is Mason's, and a tool
        that rewrote a row must not touch what he put there.
        """
        self._write(
            '| v3.13.0 | 07-15-26 | *not yet* | Committed, awaiting a sha. |\n')

        self.script.stamp_ledger_rows(
            {'v3.13.0': ('7e35a15', '07-15-26')}, dry_run=False)

        deployed, commit = self._rows()['v3.13.0']
        self.assertEqual(deployed, '07-15-26')
        self.assertEqual(commit, '`7e35a15`')

    def test_the_new_row_lands_inside_the_table(self):
        """
        ⚠️ A ROW APPENDED AFTER THE PROSE IS NOT A ROW. The file continues past
        the table with several paragraphs of explanation, and markdown ends a
        table at the first non-`|` line — so an insert in the wrong place both
        fails to register and silently breaks the rendering. Asserted by parsing
        the table the way `checks_ledger` does, from the top, and requiring the
        new row to be reached before the prose.
        """
        self._write(
            '| v3.13.0 | 07-15-26 | `7e35a15` | First row. |\n'
            '\n'
            '> A paragraph of explanation that follows the table.\n'
        )

        self.script.stamp_ledger_rows(
            {'v3.25.0': ('ea0dd36', '08-23-26')}, dry_run=False)

        lines = self.ledger.read_text(encoding='utf-8').split('\n')
        table = []
        seen_header = False
        for line in lines:
            if line.startswith('|'):
                seen_header = True
                table.append(line)
            elif seen_header:
                break

        self.assertTrue(any('v3.25.0' in line for line in table),
                        f'the new row fell outside the table:\n{table}')

    def test_a_missing_ledger_is_silent(self):
        self.script.LEDGER = Path(self.tmp.name) / 'nope.md'
        self.assertEqual(
            self.script.stamp_ledger_rows(
                {'v3.25.0': ('ea0dd36', '08-23-26')}, dry_run=False),
            [],
        )


class TheRealLedgerIsStampedTests(SimpleTestCase):
    """
    The end-to-end assertion: run the real script in report-only mode against
    the real repository and require it to have nothing to say.

    This is `src.W003` from the other side. The check says the ledger disagrees
    with git; this says the tool that fixes that has no work left — which is a
    different statement, because for four months the two could both be true.
    """

    def test_the_tool_has_nothing_left_to_do(self):
        script = _load_script()
        added = script.added_commits()
        if not added:                                   # pragma: no cover
            self.skipTest('git is not available here — nothing to compare against')

        pending = {v: (sha, '') for v, sha in added.items()}
        remaining = [
            line for line in script.stamp_ledger_rows(pending, dry_run=True)
        ]

        self.assertEqual(
            remaining, [],
            'DEPLOYED.md is missing rows or shas that `make stamp-ledger` would '
            'fill in. Run it, then commit — a FOLLOW-UP commit, because '
            '--amend changes the sha being recorded:\n  '
            + '\n  '.join(remaining),
        )
