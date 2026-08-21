"""
Deploy-time guard: does the release ledger agree with git?

⚠️ v3.19.7 — WHY THIS CHECK EXISTS, AND WHY IT TOOK FIVE INSTANCES TO BUILD.

Two lines in every changelog record facts that **do not exist while the
changelog is being written**:

    **Committed & pushed:** *not yet*
    **Deployed:** *not deployed*

Every other line in the file is finished when the author saves it. These two
become wrong a few minutes later, in the very commit that makes them wrong — and
the author is by then in `git commit`, not in the editor. It has happened on
v3.18.8, v3.19.2, v3.19.4, v3.19.5 and v3.19.6. The fifth is the clearest:
`v3.19.6.md` went into commit `aef4f73` still reading *"Committed & pushed: not
yet"*, and its `DEPLOYED.md` row said *"not committed"* — inside the commit.

**No amount of care at authoring time can fix a line whose value does not exist
until after the writing is over.** That is the whole argument for a check, and
it is why the check has to run *after* the commit rather than being a discipline
applied during it. `manage.py check` is already a deploy gate, so it runs at
exactly the right moment: on the machine that just pulled the commit.

⚠️ WHAT THIS CHECK DELIBERATELY DOES **NOT** DO
-----------------------------------------------
**It never infers whether a release was deployed.** CLAUDE.md records this trap
precisely (08-02-26): `git log --diff-filter=A` can only tell you when a
changelog FILE was committed, never whether the release SHIPPED. A run in
August 2026 used it, got a date, and concluded v3.18.1 was deployed while
`DEPLOYED.md` said *not deployed* in as many words.

    Use `--diff-filter=A` to DATE a commit.
    Use `DEPLOYED.md` to answer "is it live".

Only a human knows the second, so this check validates the *committed* half and
is silent about the *deployed* half. A guard that guesses at the thing it cannot
observe is worse than no guard, because its silence then means nothing.

⚠️ IT ALSO NEVER FAILS THE DEPLOY
---------------------------------
`CheckWarning`, not `CheckError`, and every failure path returns `[]`. A
checkout with no `.git`, a machine with no `git` binary, a shallow clone, a
rewritten history — all of them produce no output rather than blocking a deploy
on the guard's own inability to run. This follows `src.W001`'s reasoning and
`src.W002`'s correction to it: report the thing you can see, stay quiet about
the thing you cannot, and never be the reason a release does not ship.
"""
import os
import re
import subprocess

from django.conf import settings
from django.core.checks import Warning as CheckWarning, register

#: `changelogs/v3.19.6.md` → `v3.19.6`. Anything else in that directory
#: (`DEPLOYED.md`, `README.md`, `upcoming.md`) is not a release file.
_CHANGELOG_NAME = re.compile(r'^(v\d+\.\d+\.\d+)\.md$')

#: `**Committed & pushed:** …` — the claim being checked.
_COMMITTED_LINE = re.compile(
    r'^\*\*Committed(?:\s*&\s*pushed)?:?\*\*\s*(?P<value>.+?)\s*$',
    re.IGNORECASE | re.MULTILINE,
)

#: A short or long sha appearing in that line or in a DEPLOYED.md row, e.g.
#: `` `aef4f73` ``. Matched loosely because the surrounding prose varies.
_SHA = re.compile(r'`([0-9a-f]{7,40})`')

#: Phrases meaning "this has not been committed yet". Checked case-insensitively
#: against the value of the line above and against DEPLOYED.md's commit cell.
_NOT_YET = ('not yet', 'not committed', 'uncommitted', 'pending commit')

#: How long to wait for git before giving up and staying silent. `manage.py
#: check` gates a deploy; it must not hang on one.
_GIT_TIMEOUT_SECONDS = 10


def _repo_is_shallow(repo_root):
    """
    True when this checkout has truncated history, so `--diff-filter=A` lies.

    Returns True on any error as well — if git cannot be asked whether the
    history is complete, the safe reading is that it might not be. Silence is
    the correct output of a check that cannot see its subject; a verdict is not.
    """
    try:
        result = subprocess.run(
            ['git', '-C', repo_root, 'rev-parse', '--is-shallow-repository'],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    return result.stdout.strip() != 'false'


def _git_added_changelogs(repo_root):
    """
    Map `changelogs/<file>.md` → short sha of the commit that ADDED it.

    One `git log` for the whole directory rather than one per file: there are
    116 changelogs, and 116 subprocesses inside a deploy gate is its own kind of
    bug. Returns `None` — distinct from an empty dict — when git could not be
    consulted at all, so the caller can tell "no repo" from "no changelogs
    committed yet".
    """
    try:
        result = subprocess.run(
            ['git', '-C', repo_root, 'log', '--diff-filter=A',
             '--format=%x01%h', '--name-only', '--', 'changelogs/'],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
            # `GIT_OPTIONAL_LOCKS=0` — CLAUDE.md 07-09-26: read-only git
            # commands otherwise refresh the index and take `index.lock`, which
            # a sandboxed or read-only checkout may not be able to remove.
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
    except (OSError, subprocess.SubprocessError):
        # No git binary, no permission, timeout. Not this guard's business.
        return None

    if result.returncode != 0:
        # Not a repository, or a git that does not like this invocation.
        return None

    added = {}
    current_sha = None
    for line in result.stdout.splitlines():
        if line.startswith('\x01'):
            current_sha = line[1:].strip()
        elif line.strip() and current_sha:
            # First commit to add a path wins. `git log` walks newest-first, so
            # a file added, deleted and re-added reports the LATEST addition —
            # which is the right answer for "when did this file enter the tree
            # as it now stands".
            added.setdefault(line.strip(), current_sha)
    return added


def _deployed_rows(deployed_path):
    """
    Parse `DEPLOYED.md`'s table into `{version: (deployed_cell, commit_cell)}`.

    Deliberately tolerant: the file is prose with a table in it, rows carry
    long freeform notes, and a parser that demanded a strict shape would start
    reporting on the formatting rather than on the facts.
    """
    rows = {}
    try:
        with open(deployed_path, encoding='utf-8') as handle:
            for line in handle:
                if not line.startswith('|'):
                    continue
                cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
                if len(cells) < 3:
                    continue
                if not re.match(r'^v\d+\.\d+\.\d+$', cells[0]):
                    continue
                rows[cells[0]] = (cells[1], cells[2])
    except OSError:
        return None
    return rows


def _says_not_yet(text):
    lowered = text.lower()
    return any(phrase in lowered for phrase in _NOT_YET)


def _version_tuple(version):
    """`v3.19.6` → `(3, 19, 6)`, for ordering."""
    return tuple(int(part) for part in version.lstrip('v').split('.'))


def _ledger_begins_at(rows):
    """
    The oldest release `DEPLOYED.md` covers, or `None` if it covers nothing.

    ⚠️ THIS SCOPING IS THE DIFFERENCE BETWEEN A GUARD AND A NUISANCE, and the
    first run of this check is why it exists. `DEPLOYED.md` was reconstructed on
    07-31-26 and starts at v3.13.0; there are 68 changelogs older than that, all
    legitimately absent from it. Without this, the check's first output was one
    warning listing sixty-eight historical releases as defects — with three real
    findings buried in the middle of them.

    **A guard whose scope is wider than the ledger it checks reports history as
    a problem, and a guard that cries wolf on its first run is a guard everyone
    learns to skip.** That is the same lesson as the red CI gate nobody read
    (CLAUDE.md, 08-02-26), arriving from the other direction.

    Derived from the file rather than hardcoded, so backfilling an older row
    widens the check automatically and nobody has to remember a constant.
    """
    if not rows:
        return None
    return min(_version_tuple(version) for version in rows)


@register()
def release_ledger_matches_git(app_configs, **kwargs):
    """
    WARN when a changelog that git says is committed still claims it is not.

    Three things are checked, all of them about the COMMITTED half only:

      1. a committed changelog whose `**Committed & pushed:**` line still reads
         *not yet* — the five-instance failure this exists for;
      2. a committed changelog whose `DEPLOYED.md` row says *not committed*, or
         has no row at all — the v3.18.8 / v3.19.2 failure;
      3. a recorded sha that does not match the commit git says added the file —
         a copy-paste from the previous release, which is the shape the next
         instance of this will most likely take now that the first two are
         mechanically caught.

    An UNcommitted changelog saying *not yet* is correct and silent. That is the
    normal state of a release in progress, and warning about it would train
    everyone to ignore this check — which is precisely how the CI gate nobody
    read (CLAUDE.md, 08-02-26) came to be a third instance of the same lesson.
    """
    repo_root = str(settings.BASE_DIR)
    changelog_dir = os.path.join(repo_root, 'changelogs')
    if not os.path.isdir(changelog_dir):
        return []

    # ⚠️ v3.21.6 — A SHALLOW CLONE CANNOT ANSWER THIS QUESTION, AND USED TO
    # ANSWER IT CONFIDENTLY ANYWAY.
    #
    # `actions/checkout@v4` fetches depth 1 by default, so `git log
    # --diff-filter=A` sees exactly one commit and attributes the creation of
    # every file in the tree to it. In CI run #401 this check therefore reported
    # that **all twenty-five** changelogs recorded the wrong sha — "v3.18.5 says
    # `d804b6d`, git says `566aae6`", and so on down the list — which is not a
    # finding about the ledger, it is the absence of history being read as
    # disagreement.
    #
    # It printed as a warning inside the `makemigrations --check` step, so it
    # never failed the build; it simply put twenty-five confident false claims
    # into a log somebody would eventually read while diagnosing something else.
    #
    # This is the rule `scripts/pre-push.sh` states twice about its own
    # interpreter and its own scanners: **a check that cannot run must not
    # report like a check that failed.** The same sentence, one repository over.
    if _repo_is_shallow(repo_root):
        return []

    added = _git_added_changelogs(repo_root)
    if added is None:
        return []

    rows = _deployed_rows(os.path.join(changelog_dir, 'DEPLOYED.md'))
    if rows is None:
        return []

    ledger_begins_at = _ledger_begins_at(rows)
    if ledger_begins_at is None:
        return []

    stale_claim = []
    missing_row = []
    stale_row = []
    wrong_sha = []

    for filename in sorted(os.listdir(changelog_dir)):
        match = _CHANGELOG_NAME.match(filename)
        if not match:
            continue
        version = match.group(1)

        # Older than the ledger itself — see `_ledger_begins_at`.
        if _version_tuple(version) < ledger_begins_at:
            continue

        sha = added.get(f'changelogs/{filename}')
        if sha is None:
            # Not committed yet. Everything below is a claim about a commit that
            # does not exist, so there is nothing to disagree with.
            continue

        try:
            with open(os.path.join(changelog_dir, filename), encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            continue

        # ⚠️ NAMING A COMMIT IS CHECKED BEFORE SAYING "NOT YET", AND THE ORDER IS
        # THE FIX FOR THIS GUARD'S OWN FIRST BUG.
        #
        # The first version asked `"not yet" in value` first. It then reported
        # v3.18.8 and v3.19.2 — whose lines were corrected on 08-07-26 and now
        # read: *"Corrected 08-07-26 — this line read "not yet" while the
        # release was already on origin/main."* The phrase is present because
        # the line DOCUMENTS having been stale, and a substring search cannot
        # tell a claim from prose about the claim. This repo has hit that
        # exact shape before: `test_no_view_reads_the_dead_perf_cache_key`'s
        # first draft failed on a docstring that named the dead cache key while
        # explaining the bug.
        #
        # So the question asked first is the positive one — **does this line
        # name a commit?** A line that names a real sha is a finished claim
        # whatever else it says about its own history. Only a line that names no
        # commit at all can be stale.
        claim = _COMMITTED_LINE.search(text)
        if claim:
            value = claim.group('value')
            recorded = _SHA.search(value)
            if recorded:
                if not (recorded.group(1).startswith(sha)
                        or sha.startswith(recorded.group(1))):
                    wrong_sha.append(
                        f'{version} says `{recorded.group(1)}`, git says `{sha}`')
            elif _says_not_yet(value):
                stale_claim.append(f'{version} (committed in {sha})')
            # Names neither a commit nor a not-yet phrase: unparseable prose.
            # Stay quiet rather than guess — a guard that reports formatting is
            # a guard that gets skipped.

        row = rows.get(version)
        if row is None:
            missing_row.append(f'{version} (committed in {sha})')
        else:
            # Same rule for the ledger's Commit cell: a cell naming a sha is a
            # finished claim; only a cell with no sha can be stale.
            _deployed_cell, commit_cell = row
            if not _SHA.search(commit_cell) and _says_not_yet(commit_cell):
                stale_row.append(f'{version} (committed in {sha})')

    problems = []
    if stale_claim:
        problems.append(
            f'still say "not yet" under **Committed & pushed** although git has '
            f'them: {", ".join(stale_claim)}')
    if missing_row:
        problems.append(
            f'are committed but have no DEPLOYED.md row: {", ".join(missing_row)}')
    if stale_row:
        problems.append(
            f'are committed but their DEPLOYED.md row says "not committed": '
            f'{", ".join(stale_row)}')
    if wrong_sha:
        problems.append(
            f'record a commit git disagrees with: {"; ".join(wrong_sha)}')

    if not problems:
        return []

    return [
        CheckWarning(
            'The release ledger disagrees with git. Changelogs that ' +
            '; also, changelogs that '.join(problems) + '.',
            hint=(
                'These two lines record facts that do not exist until the '
                'commit is made, so they are always written stale and have to '
                'be revised afterwards — five releases running (v3.18.8, '
                'v3.19.2, v3.19.4, v3.19.5, v3.19.6). Update the changelog '
                'line and the DEPLOYED.md row now, in a follow-up commit. '
                'NOTE: this check says nothing about whether anything is '
                'DEPLOYED — git cannot know that, only you can, so the '
                '"Deployed" column is yours to maintain by hand.'
            ),
            id='src.W003',
        )
    ]
