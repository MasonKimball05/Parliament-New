"""
v3.19.7 — the test runner, and the one thing it does: clear the cache between
tests.

WHY THIS EXISTS
---------------
On 08-11-26 two careful runs of the same commit reported different numbers of
failures. The v3.19.6 batch ran `manage.py test src` in four batches and
recorded **twelve**; the 08-11 review ran the same 1,240 tests in three batches
with `--parallel=4` and got **eight**. Both numbers were honest reports of what
the runner said. Neither was a property of the code.

Isolating the difference: `src.test_login_as` passes alone and fails in one
particular grouping. `test_two_factor.TwoFactorAdminDashboardTestCase.
test_update_policy_invalid` passes alone and fails when its own class runs
(`302 != 400`). Two of the four `test_middleware_hot_path` failures behaved the
same way.

THE MECHANISM
-------------
**Django rolls back the database between tests. It does not touch the cache.**
`LocMemCache` is per-process and lives for the whole run, so a value written by
one test is visible to every later test in the same process — and which tests
share a process is decided by `--parallel` and by how modules are grouped on the
command line. Move the grouping, move the failures.

That was survivable when almost nothing was cached. It stopped being survivable
in v3.18.7, which cached the three things middleware consults on **every**
authenticated request:

    SiteSetting.get_setting        (Enforce2FAMiddleware — the 2FA policy)
    FeatureFlag.is_feature_enabled (every @require_feature_flag view)
    SystemLockdown.get_instance    (LockdownMiddleware)

Each is cached against a row that the next test's rollback has already deleted.
The result is a test running against a policy from a database that no longer
exists — and the symptom is a redirect where a 400 was expected, which reads
exactly like an authorisation bug.

Several modules already call `cache.clear()` in `setUp`. That is the same fix,
discovered one module at a time, by whoever was bitten. This makes it the
default so it does not have to be rediscovered.

⚠️ WHY A MONKEYPATCH RATHER THAN A BASE CLASS
---------------------------------------------
A `ParliamentTestCase(TestCase)` base class would be cleaner to read and would
protect only the tests that remember to inherit from it — the same shape as
every "guard written against the instance" this codebase has recorded six times.
The property wanted here is *every test*, including the ones written next year
by someone who has not read this file.

⚠️ AND WHY `run` AND NOT `_pre_setup`, WHICH IS WHERE THIS WAS FIRST WRITTEN.
`SimpleTestCase._pre_setup` is a **`classmethod`** in Django 5.2 — it builds the
class-level client and runs once per test *class*, not once per test. Hooking it
was wrong twice over: the signature is `(cls)`, so the wrapper blew up
immediately, and even fixed it would have cleared the cache once per class and
left every test after the first in a class sharing state with its siblings —
which is exactly the failure being fixed (`test_update_policy_invalid` fails
only when its own class runs).

`run(self, result=None)` is `unittest`'s per-test entry point and is not
overridden by `SimpleTestCase`, so assigning it here shadows `unittest`'s for
every Django test case and the original still runs underneath.

It is applied in `setup_test_environment`, so it exists only for the duration of
a test run and never in a live process.

⚠️ WHAT THIS DOES NOT DO. It does not make tests independent of each other in
general — module import state, `override_settings` leaks and signal receivers
are all still shared. It closes the one channel that was demonstrably moving the
failure count, and it is not a licence to stop calling `cache.clear()` where a
test needs a specific starting state.

⚠️ AND THE FILENAME IS LOAD-BEARING. This was `src/test_runner.py` first, which
matches Django's `test*.py` discovery pattern, so the runner appeared in the
list of test modules — and the first casualty was the verification of its own
fix: a comparison of two partitionings came out 24 tests apart because the file
had quietly joined the population being partitioned. Renamed to
`cache_isolated_runner.py`. **A file that changes how tests are found must not
look like a test.**
"""
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.test.runner import (DiscoverRunner, ParallelTestSuite, RemoteTestResult,
                                RemoteTestRunner, _run_subsuite)
from django.test.testcases import SimpleTestCase

#: Set once, checked so that a nested runner (or a second call to
#: `setup_test_environment`) cannot wrap `run` twice and clear the cache
#: N times per test.
_PATCHED_ATTR = '_parliament_cache_isolation_installed'

#: Backends this runner is allowed to call `.clear()` on.
#:
#: ⚠️ v3.19.8 — THE ISOLATION FIX REACHED OUTSIDE THE TEST PROCESS.
#:
#: v3.19.7 correctly identified that the cache is shared state between tests and
#: cleared every alias before every one of 1,277 of them. What it did not
#: constrain is WHICH cache. `settings.py` picks the backend from the
#: environment, not from whether a test is running:
#:
#:     if REDIS_URL and not DEBUG:
#:         CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', …}}
#:         SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
#:         SESSION_CACHE_ALIAS = 'default'
#:
#: **That alias holds the sessions.** So `manage.py test` run on the production
#: host, with the production `.env` loaded — which is the normal way anyone would
#: run it there — flushed live Redis 1,277 times and signed out every logged-in
#: member. The same hazard existed before v3.19.7 (several modules call
#: `cache.clear()` in `setUp`) but it was bounded to those modules and visible in
#: them; making it universal made it silent.
#:
#: `settings.py` now forces LocMem + DB sessions under `manage.py test`, so this
#: list should never fire. It fires anyway, loudly, because a settings change is
#: a claim and this is the check on it — under `pytest`, for instance, the
#: settings' `PYTEST_CURRENT_TEST` probe is evaluated at import time, before the
#: variable is set, so the forcing does not happen and only this does.
#:
#: > **Test isolation that reaches outside the test process is not isolation.**
_CLEARABLE_BACKENDS = (
    'django.core.cache.backends.locmem.LocMemCache',
    'django.core.cache.backends.dummy.DummyCache',
)


def _assert_caches_are_disposable():
    """
    Refuse to run at all against a cache we must not flush.

    Raised once, in `setup_test_environment`, rather than per test: the answer
    cannot change during a run, and a guard that fires 1,277 times is a guard
    someone silences.
    """
    from django.conf import settings

    for alias, config in settings.CACHES.items():
        backend = config.get('BACKEND', '')
        if backend not in _CLEARABLE_BACKENDS:
            raise ImproperlyConfigured(
                f'Refusing to run tests: cache alias "{alias}" is {backend}, and '
                f'this runner clears every alias before every test. If that is a '
                f'real Redis it is also the session store (see settings.py — '
                f'SESSION_ENGINE switches to the cache backend whenever REDIS_URL '
                f'is set and DEBUG is off), so running the suite against it would '
                f'sign out every member of the chapter.\n\n'
                f'Run the suite with REDIS_URL unset. If you genuinely need to '
                f'test a Redis-backed cache, use override_settings on the '
                f'individual test rather than pointing the whole run at it.'
            )


def _clear_all_caches():
    """Clear every configured cache alias, not just `default`."""
    for alias in caches:
        try:
            caches[alias].clear()
        except Exception:  # pragma: no cover - a broken alias must not hide a test result
            # A cache backend that cannot be cleared (an unreachable Redis in a
            # sandbox, say) must not turn every test into an error. The run is
            # still more isolated than it was.
            #
            # ⚠️ This swallow is why the backend check above is a SEPARATE, EARLY
            # assertion and not an exception raised from here. A guard that
            # swallows exceptions reports the absence of a signal as the absence
            # of a problem — CLAUDE.md records that failure three times in one
            # month — so the thing that must not be swallowed cannot live inside
            # the thing that swallows.
            pass


#: Module-level constants in `src/` that are parsed from the environment at
#: import time, and the value the SUITE must see regardless of what is in the
#: developer's `.env`.
#:
#: ⚠️ v3.21.5 — WHY THIS IS HERE, IN THE RUNNER, NEXT TO THE CACHE RESET.
#:
#: The Django CI workflow had **never once passed** — 0 successes in the 400
#: runs GitHub still retains — and the largest single cause was that fifteen
#: tests read `ADMIN_V2_USER_IDS` out of the ambient environment. `.env` is
#: gitignored; Mason's lists `73`; CI's sets nothing. So `manage.py test` was
#: green on the machine that runs the pre-push hook and red on the machine that
#: gates the merge, and the difference was a file that is not in the repository.
#:
#: That is the same defect as the cache one this module already fixes, one
#: layer out: **shared mutable state that the test run inherits rather than
#: establishes.** The cache leaked between tests; this leaks in from the host.
#: Both make the verdict a property of the environment instead of the code.
#:
#: Emptying the value here does not make the affected tests pass — it makes them
#: fail *everywhere*, deterministically, which is what forces them to patch the
#: allowlist explicitly the way eight other modules already do. A test that
#: needs an allowlist must say so.
#:
#: `src/test_environment_independence.py` walks `src/` for module-level env
#: reads and fails the build when a new one appears that is not listed here, so
#: the next one cannot be added silently.
_ENV_DERIVED_TEST_DEFAULTS = (
    # (module path, attribute, value the suite must see)
    #
    # Two halves of one allowlist: `dev_mode` re-parses the same variable rather
    # than importing `admin_v2` (import cycle), and `test_dev_mode` asserts the
    # two sets are equal — which they still are, both empty.
    ('src.view.admin_v2', 'ALLOWED_USER_IDS', set()),
    ('src.dev_mode', 'DEV_USER_IDS', set()),
    # Decides whether `serve_media` shoves bytes itself or hands nginx an
    # X-Accel-Redirect. Every test that cares patches this attribute; the ones
    # that do not care must not silently exercise the other branch because a
    # developer has the variable set.
    ('src.view.serve_media', 'MEDIA_ACCEL_PREFIX', ''),
)


def neutralise_ambient_env_config():
    """
    Force the env-derived module constants to their documented test values.

    Idempotent and import-tolerant: a module that cannot be imported in this
    process is skipped rather than turned into 1,500 errors — the enumeration
    test is what notices a missing entry, not this.
    """
    import importlib

    for module_path, attribute, value in _ENV_DERIVED_TEST_DEFAULTS:
        try:
            module = importlib.import_module(module_path)
        except Exception:  # pragma: no cover - see docstring
            continue
        if hasattr(module, attribute):
            setattr(module, attribute, value)


def install_cache_isolation():
    """
    Check the cache is disposable, then install the per-test reset. Idempotent.

    Extracted from `setup_test_environment` in v3.19.9 so that it can also be
    called from inside a parallel worker — see `_run_subsuite_isolated`.

    v3.21.5 — also neutralises the ambient environment config. It rides on this
    function deliberately: this is the one call site that both the parent
    process and every spawned worker already reach, and the failure being fixed
    (a worker that did not get the isolation) has happened here before.
    """
    neutralise_ambient_env_config()

    if getattr(SimpleTestCase, _PATCHED_ATTR, False):
        return

    # Before anything is cleared, check what would be cleared.
    _assert_caches_are_disposable()

    original_run = SimpleTestCase.run

    def run(self, result=None):
        # BEFORE the test runs, and before its `setUp`. A test that primes
        # the cache in `setUp` still gets what it primed; a test that
        # inherited someone else's cached `SiteSetting` does not.
        _clear_all_caches()
        return original_run(self, result)

    SimpleTestCase.run = run
    setattr(SimpleTestCase, _PATCHED_ATTR, True)


def _run_subsuite_isolated(args):
    """
    Django's `_run_subsuite`, preceded by installing the cache isolation in
    whatever process is about to run these tests.

    ⚠️ v3.19.9 — WITHOUT THIS, THE WHOLE FIX WAS ABSENT ON THE DEVELOPER'S OWN
    MACHINE AND SAID NOTHING ABOUT IT.

    v3.19.7 installs the `SimpleTestCase.run` patch from
    `CacheIsolatedTestRunner.setup_test_environment`, i.e. in the process that
    ran `manage.py test`. Whether a worker inherits that depends entirely on the
    multiprocessing start method:

      * **fork** (Linux default) — the worker is a memory copy of the parent, so
        the patch comes along. This is CI, and it is the sandbox the 08-13
        review measured "two partitionings agree" in.
      * **spawn** (macOS default since Python 3.8, and Windows) — the worker is
        a fresh interpreter. Django's `_init_worker` re-bootstraps it by calling
        `django.setup()` and the **module-level**
        `django.test.utils.setup_test_environment()` — *not* the runner's
        override, which it has no reference to. So nothing patches anything.

    Measured 08-15-26: `_parliament_cache_isolation_installed` is `True` in a
    forked child and **absent** in a spawned one. Parliament is developed on
    macOS. So `manage.py test --parallel` there ran with no cache isolation at
    all — the exact partitioning-dependent failure count v3.19.7 was written to
    abolish — while the file explaining the fix sat in the repo looking applied.

    `run_subsuite` is the seam Django documents for this (*"In case someone
    wants to modify these in a subclass"*), and it is the right one of the two
    available: `process_setup` fires **before** `django.setup()` in the worker,
    where importing test machinery is not safe, whereas this runs after the
    worker is fully bootstrapped and immediately before any test executes. It is
    a module-level function because `multiprocessing` pickles it by reference.

    Harmless under fork, where `install_cache_isolation` finds the patch already
    present and returns.
    """
    install_cache_isolation()
    return _run_subsuite(args)


class _SubTestDescription:
    """
    A picklable stand-in for `unittest.case._SubTest`, carrying only the text
    the parent process actually uses.

    ⚠️ A FAILING `subTest` ABORTED THE ENTIRE `--parallel` RUN AND REPORTED
    NOTHING — WITH `tblib` INSTALLED AND ITS GUARD GREEN.

        multiprocessing.pool.MaybeEncodingError: Error sending result: ...
        Reason: AttributeError("Can't pickle local object
                'convert_exception_to_response.<locals>.inner'")

    No `Ran N tests`, no `FAIL:` line, and no results for the tests that
    passed either.

    THE MECHANISM, measured 08-24-26
    --------------------------------
    `RemoteTestResult.addSubTest` puts the live `_SubTest` into the events
    list, and `_SubTest.test_case` is the running `TestCase`. Django knows
    this: `SimpleTestCase.__getstate__` exists, and its docstring says it is
    there *"to make SimpleTestCase picklable for parallel tests using
    subtests"*. It reads:

        state = super().__dict__
        if state["_outcome"]:
            ... return only the values that pass is_pickable() ...
        return state                      # ← unfiltered

    **The filtering is conditional on `_outcome`, and `_outcome` is cleared
    when the test finishes.** So:

      * at *event* time, inside the running test, `_outcome` is truthy, the
        filter drops `self.client`, and Django's own
        `check_subtest_picklable` passes — silently, printing nothing;
      * at *send* time, after the whole subsuite has finished and the pool
        pickles `result.events`, `_outcome` is `None`, `__getstate__` returns
        the raw `__dict__`, and `self.client.handler._middleware_chain` —
        `convert_exception_to_response.<locals>.inner`, a closure local to a
        function — cannot be pickled.

    Confirmed directly: the same `_SubTest` object pickles during the test and
    raises that exact `AttributeError` once `_outcome` has been cleared.

    > **A picklability check on a live object is a statement about that
    > instant, not about the object** — and here the object's own
    > `__getstate__` changes behaviour between the two instants, so the check
    > is asked in the one window where the answer is favourable.

    ⚠️ WHY EVERY EARLIER MINIMAL REPRODUCTION "DID NOT REPRODUCE", including
    v3.19.8's. `DiscoverRunner.build_suite` does
    `processes = min(self.parallel, len(subsuites))` and then
    `if processes > 1:` — and subsuites are partitioned **per TestCase
    class**. So `--parallel=8` over a single test class runs **serially**,
    with no warning. A reproduction of a parallel-only bug needs at least two
    `TestCase` classes. It is not batch-dependence and it is not flakiness;
    the earlier probes were never running in parallel at all.

    THE FIX
    -------
    Ship a description instead of the object. Only `__str__` and
    `shortDescription()` are needed: the parent replays the event into
    `TextTestResult`, which reaches the subtest solely through
    `getDescription()`.
    """

    def __init__(self, subtest):
        self._description = str(subtest)
        try:
            self._short = subtest.shortDescription()
        except Exception:                                   # pragma: no cover
            self._short = None

    def __str__(self):
        return self._description

    def shortDescription(self):
        return self._short


class PicklableSubTestResult(RemoteTestResult):
    """`RemoteTestResult` that ships a description of a failed subtest, not the subtest."""

    def addSubTest(self, test, subtest, err):
        if err is not None:
            subtest = _SubTestDescription(subtest)
        super().addSubTest(test, subtest, err)


class PicklableSubTestRunner(RemoteTestRunner):
    resultclass = PicklableSubTestResult


class CacheIsolatedParallelSuite(ParallelTestSuite):
    """`ParallelTestSuite` that installs the cache isolation inside each worker."""

    run_subsuite = _run_subsuite_isolated

    #: v3.25.2 — so a failing `subTest` can cross the process boundary.
    #: See `_SubTestDescription`.
    runner_class = PicklableSubTestRunner


class CacheIsolatedTestRunner(DiscoverRunner):
    """
    The project test runner. Identical to Django's, plus a cache reset before
    every test.

    Wired up in `Parliament/settings.py` as `TEST_RUNNER`, so `manage.py test`
    picks it up with no extra flags and CI needs no change.
    """

    #: v3.19.9 — so the isolation reaches spawned workers too. See
    #: `_run_subsuite_isolated`; without this the fix is fork-only, which means
    #: Linux-only, which means not the machine it is developed on.
    parallel_test_suite = CacheIsolatedParallelSuite

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        install_cache_isolation()
