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
from django.test.runner import DiscoverRunner
from django.test.testcases import SimpleTestCase

#: Set once, checked so that a nested runner (or a second call to
#: `setup_test_environment`) cannot wrap `run` twice and clear the cache
#: N times per test.
_PATCHED_ATTR = '_parliament_cache_isolation_installed'


def _clear_all_caches():
    """Clear every configured cache alias, not just `default`."""
    for alias in caches:
        try:
            caches[alias].clear()
        except Exception:  # pragma: no cover - a broken alias must not hide a test result
            # A cache backend that cannot be cleared (an unreachable Redis in a
            # sandbox, say) must not turn every test into an error. The run is
            # still more isolated than it was.
            pass


class CacheIsolatedTestRunner(DiscoverRunner):
    """
    The project test runner. Identical to Django's, plus a cache reset before
    every test.

    Wired up in `Parliament/settings.py` as `TEST_RUNNER`, so `manage.py test`
    picks it up with no extra flags and CI needs no change.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)

        if getattr(SimpleTestCase, _PATCHED_ATTR, False):
            return

        original_run = SimpleTestCase.run

        def run(self, result=None):
            # BEFORE the test runs, and before its `setUp`. A test that primes
            # the cache in `setUp` still gets what it primed; a test that
            # inherited someone else's cached `SiteSetting` does not.
            _clear_all_caches()
            return original_run(self, result)

        SimpleTestCase.run = run
        setattr(SimpleTestCase, _PATCHED_ATTR, True)
