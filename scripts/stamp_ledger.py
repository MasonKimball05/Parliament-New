#!/usr/bin/env python3
"""
Stamp the two release-ledger lines that cannot be written before a commit exists.

    make stamp-ledger          # rewrite them, then commit the result
    make stamp-ledger CHECK=1  # report only, change nothing

⚠️ WHY THIS EXISTS, AFTER TEN BLOCKED PUSHES.

Every changelog carries `**Committed & pushed:** …` and every `DEPLOYED.md` row
carries a Commit column. Both record the sha of the commit that *added* the
changelog — a fact that does not exist until that commit is made, so both are
necessarily written stale and have to be revised afterwards. `src.W003` catches
the drift, correctly, and the pre-push hook refuses the push.

It has refused ten of them: v3.18.8, v3.19.2, v3.19.4, v3.19.5, v3.19.6,
v3.19.7, v3.19.8, v3.19.11, the v3.20.0–v3.21.3 batch, and v3.21.4.

**The gate was never the problem.** It already knows the right answer — it
prints the sha in its own error message. What was missing is that acting on that
answer meant hand-editing two lines per release, which is exactly the sort of
mechanical edit a person defers, mistypes, or does for six of seven files.

So: the check stays, and the fix becomes one command. This script does not
decide anything. It reads `git log --diff-filter=A` — the same source
`src.W003` uses — and writes down what git already says.

⚠️ **IT DOES NOT TOUCH THE "Deployed" COLUMN**, and that is deliberate rather
than an omission. Deployment is a fact about a server; git cannot know it, and
`DEPLOYED.md` exists because a plausible-looking unverified claim is
indistinguishable from a checked one. The whole eight-report error of
07-23 → 07-31 came from a document that looked maintained. A tool that filled
in "Deployed" by inference would rebuild that trap with better ergonomics.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGELOG_DIR = REPO / 'changelogs'
LEDGER = CHANGELOG_DIR / 'DEPLOYED.md'

VERSION_FILE = re.compile(r'^v(\d+)\.(\d+)\.(\d+)\.md$')

#: Phrases either line uses to mean "not committed yet". Kept in sync with
#: `src/checks_ledger.py::_NOT_YET` — if that list grows, grow this one.
NOT_YET = ('not yet', 'not committed', 'uncommitted', 'pending commit')

#: Same pattern `src/checks_ledger.py::_SHA` uses.
SHA = re.compile(r'`([0-9a-f]{7,40})`')


def needs_stamping(text: str) -> bool:
    """
    True when a line still has to be filled in.

    ⚠️ **A PRESENT SHA WINS OVER THE WORDS, AND THIS IS NOT A DETAIL.** The
    first draft of this script tested only for the "not yet" phrases and
    reported four historical changelogs as pending — because each carries a
    correction note explaining that the line *used to* read "not yet":

        **Committed & pushed:** 08-06-26 (`f260539`). *Corrected 08-07-26 —
        this line read "not yet" while the release was already on origin/main.*

    A tool that "fixed" those would have overwritten the correction notes that
    exist precisely because this kept going wrong.

    `src.W003` learned the same lesson in v3.19.7, and its `_SHA`-before-phrase
    ordering is copied here rather than reinvented. **A scanner run over a tree
    that documents the thing being scanned finds the documentation first** —
    the fourth time that has bitten in this codebase.
    """
    if SHA.search(text):
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in NOT_YET)


def added_commits() -> dict[str, str]:
    """`{'v3.21.4': 'f3658a9'}` — the commit that ADDED each changelog."""
    result = subprocess.run(
        ['git', '-C', str(REPO), 'log', '--diff-filter=A',
         '--format=%x01%h', '--name-only', '--', 'changelogs/'],
        capture_output=True, text=True, check=False,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
    )
    if result.returncode != 0:
        return {}

    added: dict[str, str] = {}
    sha = None
    for line in result.stdout.splitlines():
        if line.startswith('\x01'):
            sha = line[1:].strip()
        elif line.strip() and sha:
            name = Path(line.strip()).name
            if VERSION_FILE.match(name):
                # `git log` walks newest-first; the first hit for a path is the
                # most recent addition, which is the right answer for "when did
                # this file enter the tree as it now stands".
                added.setdefault(name[:-3], sha)
    return added


def commit_date(sha: str) -> str:
    result = subprocess.run(
        ['git', '-C', str(REPO), 'show', '-s', '--format=%ad', '--date=format:%m-%d-%y', sha],
        capture_output=True, text=True, check=False,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
    )
    return result.stdout.strip() or ''


def stamp_changelog(version: str, sha: str, date: str, dry_run: bool) -> str | None:
    """Rewrite the `**Committed & pushed:**` line. Returns a description, or None."""
    path = CHANGELOG_DIR / f'{version}.md'
    if not path.exists():
        return None
    text = path.read_text(encoding='utf-8')

    match = re.search(r'^\*\*Committed & pushed:\*\*(.*)$', text, re.MULTILINE)
    if not match or not needs_stamping(match.group(1)):
        return None

    if not dry_run:
        path.write_text(
            text[:match.start()] + f'**Committed & pushed:** {date}, `{sha}`' + text[match.end():],
            encoding='utf-8',
        )
    return f'{version}.md  →  {date}, {sha}'


def version_tuple(version: str) -> tuple[int, int, int]:
    """`'v3.21.4'` → `(3, 21, 4)`. Mirrors `src/checks_ledger.py::_version_tuple`."""
    match = re.match(r'^v(\d+)\.(\d+)\.(\d+)$', version)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def stamp_ledger_rows(pending: dict[str, tuple[str, str]], dry_run: bool) -> list[str]:
    """
    Bring `DEPLOYED.md` into line with git. Columns: Release | Deployed | Commit | Notes.

    Two jobs, and the second one was missing until 08-23-26.

    ⚠️ **THE ONE COMMAND THE GATE RECOMMENDS DID NOT CLEAR THE GATE.**
    `src.W003` reports two different problems — a Commit cell that still says
    "not yet", and *no row at all* — and this function only ever rewrote cells
    in rows that already existed. So for a brand-new release it fixed the
    changelog line, printed "now commit them", and the next `git push` was
    refused again for the half it had not touched. That happened on the v3.25.0
    push, which is the eleventh blocked push this script exists to prevent.

    **A tool that resolves one of the two things a check reports has moved the
    failure, not fixed it** — the same shape as v3.21.7's `IntegrityError`
    replacing a `ValidationError`, and worse here because the hook's own message
    promises *"Fix it with one command"*.

    ⚠️ **A NEW ROW IS WRITTEN WITH `*not deployed*`, WHICH IS NOT A GUESS.**
    That is the honest default and the one a person writes by hand: it claims
    nothing, and it is what the Deployed column means before somebody has
    shipped the release. The rule this script opened with is unchanged — no
    inference about deployment, ever — and `*not deployed*` is the absence of a
    claim rather than a claim.
    """
    if not LEDGER.exists():
        return []
    text = LEDGER.read_text(encoding='utf-8')
    changed = []

    lines = text.split('\n')
    present: set[str] = set()
    last_row = None

    for i, line in enumerate(lines):
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 3 or not re.match(r'^v\d+\.\d+\.\d+$', cells[0]):
            continue
        present.add(cells[0])
        last_row = i
        if cells[0] not in pending or not needs_stamping(cells[2]):
            continue
        sha, _date = pending[cells[0]]
        # Rebuild only the third cell; the notes column is prose and must not be
        # reflowed by a tool that does not understand it.
        raw = line.strip().strip('|').split('|')
        raw[2] = f' `{sha}` '
        lines[i] = '|' + '|'.join(raw) + '|'
        changed.append(f'DEPLOYED.md row {cells[0]}  →  {sha}')

    # ⚠️ SCOPED TO WHAT THE LEDGER ALREADY COVERS, for the reason
    # `src/checks_ledger.py::_ledger_begins_at` gives at length: the file was
    # reconstructed on 07-31-26 and starts at v3.13.0, and there are 70-odd
    # older changelogs legitimately absent from it. A tool that backfilled those
    # would write seventy rows nobody asked for, which is the tool-shaped
    # version of a guard that cries wolf on its first run.
    if last_row is not None and present:
        begins_at = min(version_tuple(version) for version in present)
        missing = sorted(
            (version for version in pending
             if version not in present and version_tuple(version) >= begins_at),
            key=version_tuple,
        )
        for offset, version in enumerate(missing, start=1):
            sha, _date = pending[version]
            lines.insert(
                last_row + offset,
                f'| {version} | *not deployed* | `{sha}` | '
                f'See `changelogs/{version}.md`. |',
            )
            changed.append(f'DEPLOYED.md row {version}  →  NEW, `{sha}`, *not deployed*')

    if changed and not dry_run:
        LEDGER.write_text('\n'.join(lines), encoding='utf-8')
    return changed


def main() -> int:
    dry_run = bool(os.environ.get('CHECK'))

    added = added_commits()
    if not added:
        print('stamp-ledger: could not consult git — nothing checked, nothing changed.')
        return 0

    pending = {v: (sha, commit_date(sha)) for v, sha in added.items()}

    changes: list[str] = []
    for version, (sha, date) in sorted(pending.items()):
        described = stamp_changelog(version, sha, date, dry_run)
        if described:
            changes.append(described)
    changes.extend(stamp_ledger_rows(pending, dry_run))

    if not changes:
        print('stamp-ledger: every committed release is already stamped. Nothing to do.')
        return 0

    verb = 'would update' if dry_run else 'updated'
    print(f'stamp-ledger: {verb} {len(changes)} line(s):')
    for line in changes:
        print(f'    {line}')

    if dry_run:
        return 1

    print()
    print('    Now commit them — a FOLLOW-UP commit, not --amend:')
    print('      git add changelogs/ && git commit -m "Stamp release ledger"')
    print()
    print('    The "Deployed" column is deliberately untouched: git cannot know')
    print('    whether anything shipped, so that column stays yours.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
