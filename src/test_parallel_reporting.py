"""
v3.19.9 — the parallel test runner must be able to report a failure, and must
carry the cache isolation into the processes that actually run the tests.

Two findings, one file, because they are the same mistake seen twice: **a test
harness was verified in the process that launched it, not in the process that
runs the tests.**

----------------------------------------------------------------------------
1. `tblib` — without it, the suite cannot report ANY parallel failure
----------------------------------------------------------------------------
Django's parallel runner moves results between processes by pickling them. A
failure's `sys.exc_info()` tuple contains a traceback object, which is not
picklable, so `django/test/runner.py` calls `tblib.pickling_support.install()`
— guarded by `if tblib is not None`, i.e. only if tblib is importable. It was
not in `requirements.txt`, so `django.test.runner.tblib` was `None`.

The consequence is worse than a missing traceback. `RemoteTestResult.addFailure`
calls `check_picklable`, which re-raises, which kills the pool worker, which
raises in the parent as:

    TypeError: cannot pickle 'traceback' object

**and aborts the run.** No `FAIL:` line, no module, no test name, and no results
for the other 1,312 tests either. Django does print an explanatory block naming
the test, but the child prints it after the parent has already raised, so across
eight workers it is easy to lose entirely.

That is exactly what v3.19.8 §5 recorded as *"a failure reported as a pickling
error, and I could not reproduce it"* — and the reason a minimal reproduction
looked like it did not reproduce is that the symptom depends on where the
child's stdout lands relative to the parent's traceback, not on anything about
the test.

Reproduced 08-15-26 in a two-module scratch project: without tblib, one failing
`assertEqual` under `--parallel=2` produced the TypeError and no results; with
tblib, `FAILED (failures=1)` and a full traceback.

⚠️ **CORRECTION TO THE 08-13 REPORT, which said "CI runs in parallel, so there
is a class of failure the suite currently cannot report".** `.github/workflows/
ci.yml` runs `python manage.py test --verbosity=2` with no `--parallel`, so CI
is serial and was never affected. This was a developer-and-auto-run problem. The
claim was inherited rather than checked — the same shape as the eight-report
deploy-backlog error, one line long.

----------------------------------------------------------------------------
2. The cache isolation did not exist in spawned workers
----------------------------------------------------------------------------
v3.19.7 patches `SimpleTestCase.run` from
`CacheIsolatedTestRunner.setup_test_environment`, which runs in the process that
invoked `manage.py test`. Whether a worker sees that depends on the
multiprocessing start method:

  * **fork** (Linux) — the worker is a memory copy; the patch comes along.
  * **spawn** (macOS since Python 3.8, Windows) — the worker is a fresh
    interpreter, and Django's `_init_worker` re-bootstraps it with
    `django.setup()` plus the **module-level**
    `django.test.utils.setup_test_environment()`. The runner's override is never
    on that path.

Parliament is developed on macOS. So `--parallel` there had no cache isolation
at all — the exact partitioning-dependent failure count v3.19.7 exists to
abolish — and nothing said so, because the evidence for the fix ("two
partitionings agree", 08-13) was gathered on Linux.

> **A harness verified in the launching process is not verified.** Both halves
> of this file assert the property *in a fresh interpreter*, which is what a
> spawned worker is.

WHY `subprocess` AND NOT `multiprocessing`
------------------------------------------
⚠️ A test that calls `multiprocessing.get_context('spawn').Process(...)` would
be the direct reproduction and it **cannot work here**: `multiprocessing.Pool`
workers are daemonic, and daemonic processes are not allowed to have children.
So the test would pass serially and error under `--parallel`, i.e. fail in
exactly the mode it was written to protect. `subprocess` with a bare interpreter
gives the same thing a spawned worker gets — a fresh process that imports
everything from scratch — with no such constraint.
"""

import os
import subprocess
import sys

from django.test import SimpleTestCase

#: Give the child the same database and cache configuration this run uses, so
#: it cannot fail for an unrelated environmental reason.
_CHILD_ENV = {
    **os.environ,
    'DJANGO_SETTINGS_MODULE': 'Parliament.settings',
    'DB_BACKEND': 'sqlite',
    'REDIS_URL': '',
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fresh_interpreter(script):
    """Run `script` in a brand-new interpreter and return its stdout, stripped."""
    completed = subprocess.run(
        [sys.executable, '-c', script],
        cwd=_REPO_ROOT,
        env={**_CHILD_ENV, 'PYTHONPATH': _REPO_ROOT},
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f'child interpreter exited {completed.returncode}\n'
            f'--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}'
        )
    return completed.stdout.strip()


class TracebacksMustSurviveTheProcessBoundaryTests(SimpleTestCase):
    """
    ⚠️ This is a dependency assertion, not a behaviour assertion, and that is
    deliberate. The behaviour — "a failing test is reported as a failure" —
    cannot be asserted from inside the suite without deliberately failing a
    test, and a test that fails on purpose is indistinguishable from a test that
    fails. So this pins the one condition Django's own code branches on.
    """

    def test_django_can_pickle_tracebacks(self):
        from django.test import runner as django_runner

        self.assertIsNotNone(
            django_runner.tblib,
            '\n\n'
            '    tblib is not installed in this environment.\n\n'
            '    Run:  pip install -r requirements.txt\n\n'
            'It was added in v3.19.9, so this is expected on the first run '
            'after pulling that release and it is the only thing to do about '
            'it. Without tblib, `manage.py test --parallel` cannot pickle a '
            'failure across the process boundary: the whole run aborts with '
            '"TypeError: cannot pickle \'traceback\' object" and reports no '
            'results at all, including for the tests that passed.',
        )

    def test_tblib_is_pinned_in_requirements(self):
        """
        Installed-but-unpinned is the state that regresses on the next clean
        checkout, which is the server and CI.
        """
        with open(os.path.join(_REPO_ROOT, 'requirements.txt')) as fh:
            requirements = fh.read()
        self.assertIn('tblib', requirements)

    def test_a_traceback_actually_round_trips(self):
        """
        The mechanism, not the import. `tblib` being importable is necessary;
        `pickling_support.install()` having been called is what makes the
        round-trip work, and Django does that in `RemoteTestResult.__init__`.

        ⚠️ SKIPPED RATHER THAN FAILED WHEN tblib IS ABSENT, deliberately. The
        requirement is stated once, by `test_django_can_pickle_tracebacks`, with
        one remedy. A second test failing for the same missing package gives the
        reader two problems and one cause — and in a push gate that is the
        difference between a message someone acts on and a message someone
        bypasses.
        """
        import pickle

        from django.test import runner as django_runner
        from django.test.runner import RemoteTestResult

        if django_runner.tblib is None:
            self.skipTest('tblib absent — reported by test_django_can_pickle_tracebacks')

        RemoteTestResult()  # installs tblib's pickling support as a side effect
        try:
            raise ValueError('a failure that has to cross a process boundary')
        except ValueError:
            err = sys.exc_info()

        restored = pickle.loads(pickle.dumps(err))  # nosec B301  # round-trips a value created on the same line; nothing untrusted is deserialized
        self.assertIs(restored[0], ValueError)
        self.assertEqual(str(restored[1]), 'a failure that has to cross a process boundary')
        self.assertIsNotNone(restored[2], 'the traceback did not survive pickling')


class CacheIsolationMustReachSpawnedWorkersTests(SimpleTestCase):

    #: What Django's `_init_worker` does in a spawned worker, and nothing more.
    _WHAT_A_SPAWNED_WORKER_DOES = (
        'import django;'
        'django.setup();'
        'from django.test.utils import setup_test_environment;'
        'setup_test_environment();'
        'from django.test.testcases import SimpleTestCase;'
        'print(getattr(SimpleTestCase, "_parliament_cache_isolation_installed", "ABSENT"))'
    )

    def test_the_runner_hook_alone_does_not_reach_a_fresh_interpreter(self):
        """
        THE NEGATIVE CONTROL, and it is the finding.

        A fresh interpreter that does exactly what Django's spawn worker does
        gets no patch — which is why `setup_test_environment` on the runner
        cannot be the only place it is installed. If this ever starts printing
        `True`, Django has changed how it bootstraps workers and the wrapper
        below may have become unnecessary; it has NOT become wrong.
        """
        self.assertEqual(
            _fresh_interpreter(self._WHAT_A_SPAWNED_WORKER_DOES),
            'ABSENT',
        )

    def test_the_worker_entry_point_installs_it_in_a_fresh_interpreter(self):
        """The fix: the thing a worker actually calls installs the patch."""
        script = (
            'import django;'
            'django.setup();'
            'from django.test.utils import setup_test_environment;'
            'setup_test_environment();'
            'import sys; sys.argv = ["manage.py", "test"];'
            'from src.cache_isolated_runner import install_cache_isolation;'
            'install_cache_isolation();'
            'from django.test.testcases import SimpleTestCase;'
            'print(getattr(SimpleTestCase, "_parliament_cache_isolation_installed", "ABSENT"))'
        )
        self.assertEqual(_fresh_interpreter(script), 'True')

    def test_the_runner_uses_a_parallel_suite_that_installs_it(self):
        """
        The wiring, asserted as a property rather than by name: whatever the
        runner hands to the pool must not be Django's stock suite, and its
        `run_subsuite` must be ours.
        """
        from django.test.runner import ParallelTestSuite, _run_subsuite

        from src.cache_isolated_runner import (
            CacheIsolatedTestRunner,
            _run_subsuite_isolated,
        )

        suite_class = CacheIsolatedTestRunner.parallel_test_suite
        self.assertIsNot(
            suite_class, ParallelTestSuite,
            'the runner is handing Django its stock ParallelTestSuite, so a '
            'spawned worker runs with no cache isolation',
        )
        self.assertTrue(issubclass(suite_class, ParallelTestSuite))
        self.assertIsNot(suite_class.run_subsuite, _run_subsuite)
        self.assertIs(suite_class.run_subsuite, _run_subsuite_isolated)

    def test_the_worker_wrapper_is_picklable_by_reference(self):
        """
        `multiprocessing` pickles `run_subsuite` to send it to the pool, and a
        closure or a lambda would fail there — under spawn only, i.e. on the
        machine this was written to fix and not on the one it was tested on.
        """
        import pickle

        from src.cache_isolated_runner import CacheIsolatedParallelSuite

        restored = pickle.loads(pickle.dumps(CacheIsolatedParallelSuite.run_subsuite))  # nosec B301  # round-trips a value created on the same line; nothing untrusted is deserialized
        from src.cache_isolated_runner import _run_subsuite_isolated
        self.assertIs(restored, _run_subsuite_isolated)

    def test_installing_twice_does_not_stack_the_wrapper(self):
        """
        The worker path and the runner path can both fire in one process (under
        fork they do). Two wrappers would clear the cache twice per test —
        harmless — but a third would be the tell that the idempotence guard had
        been dropped, and that guard is also what stops recursion.
        """
        from django.test.testcases import SimpleTestCase as DjangoSimpleTestCase

        from src.cache_isolated_runner import install_cache_isolation

        before = DjangoSimpleTestCase.run
        install_cache_isolation()
        install_cache_isolation()
        self.assertIs(DjangoSimpleTestCase.run, before)
