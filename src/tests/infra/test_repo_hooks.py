"""
v3.19.9 — the pre-push hook exists, is argued for, and has never run.

WHAT WAS FOUND
--------------
`scripts/pre-push.sh` was written on 07-18-26 (v3.14.1) to refuse a push whose
test suite is red, on the entirely correct reasoning that *prod deploys are `git
pull` of main, so an untested push is one `git pull` away from prod.* `make
hooks` installs it.

`.git/hooks/` in this working tree contains nothing but Git's own `.sample`
files, and `core.hooksPath` is unset. **The hook has never been installed.**

The cost is legible in this repo's own history:

  * `test_url_smoke` was red from 07-30 to 08-02 and was found by a nightly
    review, not by the push that broke it;
  * v3.19.6 ran the full suite for the first time in eight batches and found 12
    pre-existing failures, all reproducible on the already-pushed commit;
  * seven consecutive releases were pushed with a stale ledger line, which
    v3.19.9 also adds to the hook.

> **A guard needs a trigger it does not have to be remembered — and so does the
> trigger.** v3.19.8 stated the first half and applied it to `src.W003` by
> moving it into `manage.py preflight`. This is the same sentence one level up:
> the hook is the trigger, `make hooks` is the trigger for the trigger, and
> nothing checked it. A one-time manual setup step is indistinguishable, six
> months later, from a setup step that was done.

WHY THIS TEST IS THE WEAKER HALF, SAID PLAINLY
-----------------------------------------------
A git hook is local configuration. It lives in `.git/`, which is not tracked, so
**no commit can deliver it** — there is no version of this fix that installs
itself. What a test can do is make the absence *visible from inside the thing
the hook guards*, so it is reported by every suite run, every nightly auto-run,
and the developer's own `make test`, instead of by a review three days later.

The remedy is one command and the failure message says so.

⚠️ SKIPPED WHERE THE HOOK WOULD BE WRONG TO REQUIRE. CI checks out a fresh tree
and never pushes from it, and a deployment checkout has no business having
developer hooks either. The skip is on `CI` and on the absence of a `.git`
directory, and it is deliberately NOT on "the file is missing" — skipping when
the thing you are asserting is absent is how a guard becomes decorative.
"""

import os
import subprocess

from django.test import SimpleTestCase

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SOURCE = os.path.join(_REPO_ROOT, 'scripts', 'pre-push.sh')


def _git_dir():
    """The real `.git` directory, or None if this is not a git working tree."""
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
            env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    path = out.stdout.strip()
    return path if os.path.isabs(path) else os.path.join(_REPO_ROOT, path)


def _hooks_dir():
    out = subprocess.run(
        ['git', 'config', '--get', 'core.hooksPath'],
        cwd=_REPO_ROOT, capture_output=True, text=True, timeout=30,
        env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
    )
    configured = out.stdout.strip()
    if configured:
        return configured if os.path.isabs(configured) else os.path.join(_REPO_ROOT, configured)
    git_dir = _git_dir()
    return os.path.join(git_dir, 'hooks') if git_dir else None


class ThePrePushHookIsInstalledTests(SimpleTestCase):

    def setUp(self):
        if os.getenv('CI'):
            self.skipTest('CI checks out a fresh tree and never pushes from it')
        if _git_dir() is None:
            self.skipTest('not a git working tree (deployment checkout)')

    def test_the_hook_source_is_still_in_the_repo(self):
        """
        The control, and it is not trivial: if `scripts/pre-push.sh` is ever
        deleted, the test below would start failing for a reason that has
        nothing to do with installation, and someone would 'fix' it by deleting
        the test.
        """
        self.assertTrue(os.path.isfile(_SOURCE), f'{_SOURCE} is missing')

    def test_the_hook_is_installed_and_current(self):
        hooks = _hooks_dir()
        self.assertIsNotNone(hooks, 'could not locate the git hooks directory')
        installed = os.path.join(hooks, 'pre-push')

        remedy = (
            '\n\nRun:  make hooks\n\n'
            'This hook refuses to push a tree whose test suite is red or whose '
            'release ledger is stale. It was written 07-18-26 and never '
            'installed, which is why `test_url_smoke` stayed red across several '
            'pushes from 07-30 to 08-02, why v3.19.6 found 12 pre-existing '
            'failures on an already-pushed commit, and why seven consecutive '
            'releases shipped a stale changelog line. '
            "(`git push --no-verify` still bypasses it when you mean to.)"
        )

        self.assertTrue(
            os.path.isfile(installed),
            f'the pre-push hook is not installed at {installed}.{remedy}',
        )

        with open(_SOURCE) as fh:
            expected = fh.read()
        with open(installed) as fh:
            actual = fh.read()
        self.assertEqual(
            actual, expected,
            'the installed pre-push hook is out of date with '
            f'scripts/pre-push.sh.{remedy}',
        )

    def test_the_installed_hook_is_executable(self):
        """
        Git silently ignores a hook without the execute bit — no error, no
        output, no push blocked. Exactly the failure mode this file is about,
        one permission bit down.
        """
        hooks = _hooks_dir()
        installed = os.path.join(hooks, 'pre-push') if hooks else None
        if not installed or not os.path.isfile(installed):
            self.skipTest('covered by test_the_hook_is_installed_and_current')
        self.assertTrue(
            os.access(installed, os.X_OK),
            f'{installed} is not executable, so git ignores it silently. '
            'Run: make hooks',
        )


class TheHookGatesWhatItClaimsToGateTests(SimpleTestCase):
    """
    A hook that runs and checks the wrong things is worse than none, because it
    is believed. These assert the script's content rather than its behaviour —
    running it would run the whole suite recursively.
    """

    def test_it_runs_the_test_suite(self):
        with open(_SOURCE) as fh:
            script = fh.read()
        self.assertIn('manage.py test src', script)

    def test_it_does_not_invoke_manage_py_with_a_bare_interpreter(self):
        """
        ⚠️ THIS IS A REGRESSION TEST FOR THE FIRST PUSH THIS HOOK EVER BLOCKED.

        The original script ran `python3 manage.py test`. A git hook runs in a
        non-interactive, non-login shell **with no virtualenv activated**, so
        `python3` is the system interpreter — which on macOS has no Django in
        it. The hook died on `ImportError` and printed "tests failed", so a
        broken environment was reported in the same words as a broken tree.

        **A check that cannot run must not report like a check that failed.**
        The script now resolves an interpreter that can import Django, and
        treats "found none" as a loud skip rather than a block — because a hook
        that blocks every push for an environment reason is a hook that gets
        deleted, which is exactly how this repo went a month without one.
        """
        with open(_SOURCE) as fh:
            script = fh.read()

        self.assertNotIn(
            'python3 manage.py', script,
            'the hook invokes manage.py with a bare `python3`, which in a git '
            "hook's environment is the system interpreter and has no Django",
        )
        self.assertIn('.venv/bin/python', script)
        self.assertIn('VIRTUAL_ENV', script)
        self.assertIn(
            'PARLIAMENT_PYTHON', script,
            'there must be an escape hatch for a non-standard interpreter path',
        )

    def test_it_gates_on_the_release_ledger_checks(self):
        """
        v3.19.9 added this half. `src.W003` fires on a line that can only be
        written stale, and the push is the last moment at which fixing it is an
        `--amend` rather than a follow-up commit.
        """
        with open(_SOURCE) as fh:
            script = fh.read()
        self.assertIn('RELEASE_GATING_CHECK_IDS', script)

    def test_it_runs_the_two_security_scans_ci_runs(self):
        """
        v3.19.10 added this half, and the reason is the sharpest instance of
        this module's own subject in the repo's history.

        CI's `security` job runs bandit and pip-audit on every push, neither
        step carrying `continue-on-error`. **The bandit step exited 1
        continuously from 07-29-26 to 08-17-26** — nineteen days, roughly a
        dozen pushes. Nothing swallowed the signal; GitHub rendered a red ❌
        every time and nobody downstream read it.

        ⚠️ And this hook — built one release earlier for precisely the pattern
        "a check whose trigger is somebody remembering is not triggered" — ran
        the suite and the ledger checks and **not these**. A trigger built in
        response to a pattern still has to be pointed at every instance of it.
        """
        with open(_SOURCE) as fh:
            script = fh.read()

        self.assertIn('bandit', script, 'the hook does not run bandit')
        self.assertIn('pip_audit', script, 'the hook does not run pip-audit')

    def test_its_bandit_flags_match_the_ones_ci_uses(self):
        """
        ⚠️ THE FAILURE MODE THIS PINS IS WORSE THAN NOT RUNNING BANDIT AT ALL.

        If the hook scans with a laxer threshold or a wider exclude than CI, it
        reports green on pushes CI then rejects — and a local gate that
        disagrees with the remote one is a local gate people stop believing,
        which is how a gate becomes decoration.

        `-ll` is the load-bearing flag: it is what makes MEDIUM a failure rather
        than a note, and it is the flag the CI file's own comment says was
        deliberately flipped on 07-08-26.
        """
        with open(_SOURCE) as fh:
            script = fh.read()
        with open(os.path.join(_REPO_ROOT, '.github', 'workflows', 'ci.yml')) as fh:
            ci = fh.read()

        for flag in ('-r src/', '-ll', '--exclude src/migrations'):
            with self.subTest(flag=flag):
                self.assertIn(
                    flag, ci,
                    f'CI no longer passes {flag!r} to bandit — this test is '
                    f'pinning the hook to a command CI has moved off.',
                )
                self.assertIn(
                    flag, script,
                    f'the hook scans without {flag!r} while CI scans with it, '
                    f'so the hook can pass a push CI will fail.',
                )

    def test_a_scan_that_cannot_run_does_not_abort_the_push(self):
        """
        The rule stated at the top of `pre-push.sh`, asserted rather than
        trusted: **a check that cannot run must not report like a check that
        failed.** A missing binary or an offline laptop is a loud skip; only a
        real finding is an `exit 1`.

        ⚠️ Its mirror is asserted too, and the first draft got it wrong: the
        block ended with "all gates green" after skipping both scans. A check
        that cannot run must not report like a check that **passed** either, and
        the summary line is the only line most pushes are read for.
        """
        with open(_SOURCE) as fh:
            script = fh.read()

        self.assertIn('_skipped=1', script, 'skips are not tracked')
        self.assertIn(
            'SKIPPED (see above)', script,
            'the summary line cannot distinguish "checked and clean" from '
            '"not checked"',
        )
        for phrase in ('bandit not installed', 'pip-audit could not report'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, script)

    def test_the_gating_set_it_imports_actually_exists(self):
        """
        The hook reaches into `preflight` for the set, so a rename there breaks
        the hook — silently, at push time, on a machine with no test running.
        """
        from src.management.commands.preflight import RELEASE_GATING_CHECK_IDS

        self.assertIn('src.W003', RELEASE_GATING_CHECK_IDS)
        self.assertIn('src.W002', RELEASE_GATING_CHECK_IDS)
