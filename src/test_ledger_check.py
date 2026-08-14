"""
v3.19.7 — tests for `src.W003`, the release-ledger check.

⚠️ THESE TESTS BUILD A THROWAWAY GIT REPOSITORY RATHER THAN READING THIS ONE.

Pointing them at the real `changelogs/` would make them a test of today's
ledger: green while it happens to be tidy, red the next time a release is
half-recorded — which is a normal, correct, transient state that the check is
supposed to *report*, not a test failure. It would also make the suite fail on
the very condition the check exists to announce, which is the fastest way to
train everyone to ignore both.

So each test constructs the exact world it is about — a real `git init`, real
commits, real `git log` output — and asserts what the check says about it. The
subprocess is real on purpose: this check's entire job is to reconcile a file
against git, and a mocked `git log` would test the parser against a fixture
written by the same person who wrote the parser.

⚠️ AND THE CHECK'S FIRST TWO REAL RUNS BOTH FOUND BUGS IN THE CHECK, which is
why the negative cases below outnumber the positive ones:

  1. It reported **68 historical releases** as missing from `DEPLOYED.md`. They
     are missing because the ledger was reconstructed on 07-31-26 and starts at
     v3.13.0. Scope now derives from the ledger's own oldest row.
  2. It reported **v3.18.8 and v3.19.2**, whose lines were corrected months ago
     and now read *"Corrected 08-07-26 — this line read 'not yet' while the
     release was already on origin/main."* A substring search cannot tell a
     claim from prose about the claim. The check now asks the positive question
     first: does the line name a commit?

Both are the same failure in different clothes — **a guard that reports things
that are fine is a guard nobody reads** — and both are covered below.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from django.test import SimpleTestCase

from src import checks_ledger


def _git(repo, *args):
    subprocess.run(
        ['git', '-C', repo, *args],
        capture_output=True, text=True, check=True,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0',
             'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.com',
             'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.com'},
    )


def _git_available():
    try:
        subprocess.run(['git', '--version'], capture_output=True, timeout=5, check=True)
    except Exception:
        return False
    return True


@unittest.skipUnless(_git_available(), 'git is not available in this environment')
class LedgerCheckTests(SimpleTestCase):
    """The check, exercised against purpose-built repositories."""

    LEDGER_HEADER = (
        '| Release | Deployed | Commit | Notes |\n'
        '|---|---|---|---|\n'
    )

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, True)
        os.makedirs(os.path.join(self.repo, 'changelogs'))
        _git(self.repo, 'init', '-q')

    # -- helpers ---------------------------------------------------------

    def _write(self, name, body):
        path = os.path.join(self.repo, 'changelogs', name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(body)
        return path

    def _ledger(self, *rows):
        self._write('DEPLOYED.md', self.LEDGER_HEADER + ''.join(rows))

    def _commit(self, message='c'):
        _git(self.repo, 'add', '-A')
        _git(self.repo, 'commit', '-q', '-m', message)
        result = subprocess.run(
            ['git', '-C', self.repo, 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def _run(self):
        """Run the check with BASE_DIR pointed at the throwaway repo."""
        with self.settings(BASE_DIR=self.repo):
            return checks_ledger.release_ledger_matches_git(None)

    def _messages(self):
        return ' '.join(warning.msg for warning in self._run())

    # -- the failure this check exists for -------------------------------

    def test_a_committed_changelog_still_saying_not_yet_is_reported(self):
        """The five-instance bug, in one test."""
        self._write('v1.0.0.md', '**Committed & pushed:** *not yet*\n')
        self._ledger('| v1.0.0 | *not deployed* | *not committed* | x |\n')
        self._commit()

        warnings = self._run()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].id, 'src.W003')
        self.assertIn('v1.0.0', warnings[0].msg)

    def test_an_uncommitted_changelog_saying_not_yet_is_silent(self):
        """
        ⚠️ THE MOST IMPORTANT NEGATIVE CONTROL IN THIS FILE. A release in
        progress says "not yet" because it IS not yet, and that is the state
        every changelog passes through. Warning here would fire on every
        in-flight release and the check would be ignored within a week.
        """
        self._ledger('| v1.0.0 | *not deployed* | `abc1234` | x |\n')
        self._commit()  # ledger committed, changelog is not
        self._write('v1.0.0.md', '**Committed & pushed:** *not yet*\n')

        self.assertEqual(self._run(), [])

    def test_a_committed_changelog_naming_its_commit_is_silent(self):
        self._write('v1.0.0.md', '**Committed & pushed:** 01-01-26 (`0000000`)\n')
        self._ledger('| v1.0.0 | *not deployed* | `0000000` | x |\n')
        sha = self._commit()

        # The placeholder sha is wrong on purpose in the next test; here we
        # rewrite it to the real one and expect silence.
        self._write('v1.0.0.md', f'**Committed & pushed:** 01-01-26 (`{sha}`)\n')
        self._ledger(f'| v1.0.0 | *not deployed* | `{sha}` | x |\n')
        self.assertEqual(self._run(), [])

    # -- the two bugs the check's own first runs exposed ------------------

    def test_a_correction_note_quoting_not_yet_is_not_a_finding(self):
        """
        ⚠️ BUG #2, FOUND ON THE SECOND REAL RUN. v3.18.8 and v3.19.2 read:

            **Committed & pushed:** 08-06-26 (`f260539`). *Corrected 08-07-26 —
            this line read "not yet" while the release was already on
            origin/main.*

        The phrase is there because the line documents having been stale. A
        substring search cannot tell a claim from prose about the claim — the
        same shape as `test_no_view_reads_the_dead_perf_cache_key`'s first
        draft, which failed on a docstring naming the dead cache key while
        explaining the bug. The check now asks "does this name a commit?" first.
        """
        self._write('v1.0.0.md', '**Committed & pushed:** placeholder\n')
        self._ledger('| v1.0.0 | *not deployed* | `0000000` | x |\n')
        sha = self._commit()

        self._write(
            'v1.0.0.md',
            f'**Committed & pushed:** 01-01-26 (`{sha}`). *Corrected later — '
            f'this line read "not yet" while the release was already pushed.*\n')
        self._ledger(f'| v1.0.0 | *not deployed* | `{sha}` | x |\n')

        self.assertEqual(self._run(), [])

    def test_releases_older_than_the_ledger_are_out_of_scope(self):
        """
        ⚠️ BUG #1, FOUND ON THE FIRST REAL RUN — one warning naming **68**
        historical releases, with three real findings buried inside it.
        `DEPLOYED.md` was reconstructed on 07-31-26 and starts at v3.13.0;
        everything older is legitimately absent.

        Scope is derived from the ledger's oldest row rather than hardcoded, so
        backfilling an old row widens the check by itself.
        """
        self._write('v0.9.0.md', '**Committed & pushed:** *not yet*\n')   # ancient
        self._write('v2.0.0.md', '**Committed & pushed:** *not yet*\n')   # in scope
        self._ledger('| v2.0.0 | *not deployed* | *not committed* | x |\n')
        self._commit()

        messages = self._messages()
        self.assertIn('v2.0.0', messages)
        self.assertNotIn('v0.9.0', messages)

    # -- the other two arms ----------------------------------------------

    def test_a_committed_changelog_with_no_ledger_row_is_reported(self):
        """The v3.18.8 / v3.19.2 failure of 08-06-26: no row at all."""
        self._write('v2.0.0.md', '**Committed & pushed:** 01-01-26 (`0000000`)\n')
        self._write('v2.1.0.md', '**Committed & pushed:** 01-01-26 (`0000000`)\n')
        self._ledger('| v2.0.0 | *not deployed* | `0000000` | x |\n')
        self._commit()

        messages = self._messages()
        self.assertIn('v2.1.0', messages)
        self.assertIn('no DEPLOYED.md row', messages)

    def test_a_recorded_sha_that_git_disagrees_with_is_reported(self):
        """
        A copy-paste from the previous release. This is the shape the next
        instance is most likely to take, now that the blank "not yet" case is
        mechanically caught.
        """
        self._write('v2.0.0.md', '**Committed & pushed:** 01-01-26 (`deadbee`)\n')
        self._ledger('| v2.0.0 | *not deployed* | `deadbee` | x |\n')
        sha = self._commit()

        messages = self._messages()
        self.assertIn('deadbee', messages)
        self.assertIn(sha, messages)

    # -- never break a deploy --------------------------------------------

    def test_a_directory_that_is_not_a_repository_is_silent(self):
        """
        ⚠️ A DEPLOYED CHECKOUT MAY HAVE NO `.git`, AND `manage.py check` GATES
        THE DEPLOY. The check must produce nothing rather than block a release
        on its own inability to run — `src.W001`'s reasoning, and `src.W002`'s
        correction to it: report what you can see, stay quiet about what you
        cannot, and never be the reason a release does not ship.
        """
        plain = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, plain, True)
        os.makedirs(os.path.join(plain, 'changelogs'))
        with open(os.path.join(plain, 'changelogs', 'v1.0.0.md'), 'w') as handle:
            handle.write('**Committed & pushed:** *not yet*\n')

        with self.settings(BASE_DIR=plain):
            self.assertEqual(checks_ledger.release_ledger_matches_git(None), [])

    def test_a_missing_changelogs_directory_is_silent(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        with self.settings(BASE_DIR=empty):
            self.assertEqual(checks_ledger.release_ledger_matches_git(None), [])

    def test_an_empty_ledger_is_silent(self):
        """
        No rows means no scope. Warning about every changelog because the ledger
        has not been started yet would be the 68-release failure again, at
        maximum volume.
        """
        self._write('v1.0.0.md', '**Committed & pushed:** *not yet*\n')
        self._ledger()
        self._commit()

        self.assertEqual(self._run(), [])

    # -- the thing it must never claim -----------------------------------

    def test_the_check_says_nothing_about_deployment(self):
        """
        ⚠️ CLAUDE.md, 08-02-26: `git log --diff-filter=A` can only date a
        changelog FILE's commit, never tell you whether the release SHIPPED. A
        run used it, got a date, and concluded v3.18.1 was deployed while
        `DEPLOYED.md` said *not deployed* in as many words.

        So: a release whose commit half is fully recorded and whose Deployed
        column reads *not deployed* is a completely normal state, and this check
        must be silent about it. Only a human knows the second half.
        """
        self._write('v2.0.0.md', '**Committed & pushed:** 01-01-26 (`0000000`)\n')
        self._ledger('| v2.0.0 | *not deployed* | `0000000` | x |\n')
        sha = self._commit()
        self._write('v2.0.0.md', f'**Committed & pushed:** 01-01-26 (`{sha}`)\n')
        self._ledger(f'| v2.0.0 | *not deployed* | `{sha}` | x |\n')

        self.assertEqual(self._run(), [])


# ---------------------------------------------------------------------------
# v3.19.8 — the trigger
# ---------------------------------------------------------------------------

class TheLedgerCheckActuallyGatesTheDeployTests(SimpleTestCase):
    """
    v3.19.8 — `src.W003` works and nothing ran it.

    v3.19.7 built the check with the right argument: *no amount of care at
    authoring time can fix a line whose value does not exist until after the
    writing is over*, so the response has to run AFTER the commit. It then left
    the running to `manage.py check`, which prints warnings and exits 0.

    The result was visible two days later. v3.19.7 was committed on 08-13-26
    with its own ledger lines reading "not yet"; `src.W003` reported it
    correctly, to nobody, until the nightly review ran `manage.py check` by
    hand. Sixth release running.

    > **A guard needs a trigger it does not have to be remembered.**

    `manage.py preflight` already gates deploys and cron, so these assert the
    promotion rather than re-testing the check: `src.W002` and `src.W003` become
    ERRORS there, and every other warning stays a warning — promoting all of
    them would make preflight noisy, and a preflight nobody reads is the exact
    failure mode it exists to avoid.
    """

    def _run_against(self, messages):
        from unittest import mock

        from src.management.commands.preflight import Command

        command = Command()
        command.stdout = mock.MagicMock()
        with mock.patch('django.core.checks.run_checks', return_value=messages):
            command.check_system_checks()
        return command

    def test_a_stale_ledger_fails_preflight_rather_than_warning_it(self):
        from django.core.checks import Warning as DjangoWarning

        command = self._run_against([DjangoWarning('ledger disagrees with git', id='src.W003')])

        self.assertTrue(any('src.W003' in e for e in command.errors))
        self.assertEqual(command.warnings, [])

    def test_an_unmigrated_kai_schema_fails_preflight_too(self):
        from django.core.checks import Warning as DjangoWarning

        command = self._run_against([DjangoWarning('Kai tables not queryable', id='src.W002')])

        self.assertTrue(any('src.W002' in e for e in command.errors))

    def test_an_ordinary_warning_stays_a_warning(self):
        """
        The negative control, and the one that keeps this usable. Promoting
        every warning would turn the deploy gate into something people pass with
        `--force`, which is the same as not having it.
        """
        from django.core.checks import Warning as DjangoWarning

        command = self._run_against([DjangoWarning('some style advice', id='models.W042')])

        self.assertEqual(command.errors, [])
        self.assertTrue(any('models.W042' in w for w in command.warnings))

    def test_a_real_error_still_fails(self):
        from django.core.checks import Error as DjangoError

        command = self._run_against([DjangoError('something is broken', id='admin.E002')])

        self.assertTrue(any('admin.E002' in e for e in command.errors))

    def test_a_clean_run_reports_a_pass(self):
        """
        Silence must be recorded as a pass, not as an absent check — an
        omitted line reads identically to a check that did not run.
        """
        command = self._run_against([])

        self.assertEqual(command.errors, [])
        self.assertEqual(command.warnings, [])
        self.assertTrue(any('System checks' in p for p in command.passed))
