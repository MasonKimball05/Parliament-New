"""
v3.19.8 — the test runner must not be able to flush a real cache.

WHY THIS FILE EXISTS
--------------------
v3.19.7 fixed a genuine problem: the suite's failure count depended on how it was
partitioned, because Django rolls back the database between tests and never
touches the cache, and since v3.18.7 the cache holds the 2FA policy, every
feature flag and the lockdown state. The fix — clear every alias before every
test — is right.

What it did not constrain is WHICH cache. `settings.py` chooses the backend from
the environment:

    if REDIS_URL and not DEBUG:
        CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', …}}
        SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
        SESSION_CACHE_ALIAS = 'default'

**The cache alias is the session store.** The `if 'test' in sys.argv` block two
hundred lines below set Celery to eager and said nothing about `CACHES`, so
`manage.py test` on the production host — with the production `.env` loaded,
which is the normal way anyone would run it there — cleared live Redis 1,277
times and signed out every member of the chapter.

The same hazard predates v3.19.7: several modules call `cache.clear()` in
`setUp`. But it was bounded to those modules and visible in them. Making it
universal made it silent, and inherited by every test written from now on.

> **Test isolation that reaches outside the test process is not isolation.** The
> runner was right that the cache is shared state; the same sentence is true of
> the cache one environment over.

TWO HALVES, AND EACH CHECKS THE OTHER
-------------------------------------
1. `settings.py` forces LocMem + DB sessions under `manage.py test`. This is the
   stronger half — it also stops a test run READING production cache state,
   which is a correctness problem before it is a safety one.
2. `_assert_caches_are_disposable()` refuses to start against anything else.

A settings change is a claim; (2) is the check on it. It is not redundant: the
settings' test probe is `'test' in sys.argv or os.getenv('PYTEST_CURRENT_TEST')`,
and `PYTEST_CURRENT_TEST` is set during test EXECUTION — after settings import —
so under pytest the forcing does not happen and only the runner's check stands
between the suite and Redis.
"""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from src.cache_isolated_runner import _assert_caches_are_disposable

_LOCMEM = 'django.core.cache.backends.locmem.LocMemCache'
_REDIS = 'django_redis.cache.RedisCache'


class TheRunnerRefusesToFlushARealCacheTests(SimpleTestCase):

    @override_settings(CACHES={'default': {'BACKEND': _LOCMEM, 'LOCATION': 'x'}})
    def test_locmem_is_accepted(self):
        """The control. A guard that refused everything would pass the rest."""
        _assert_caches_are_disposable()

    @override_settings(CACHES={'default': {'BACKEND': _REDIS, 'LOCATION': 'redis://prod:6379/0'}})
    def test_a_redis_default_alias_is_refused(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _assert_caches_are_disposable()
        self.assertIn('sign out every member', str(ctx.exception))

    @override_settings(CACHES={
        'default': {'BACKEND': _LOCMEM, 'LOCATION': 'x'},
        'sessions': {'BACKEND': _REDIS, 'LOCATION': 'redis://prod:6379/1'},
    })
    def test_a_non_default_alias_is_refused_too(self):
        """
        `_clear_all_caches` iterates EVERY alias, so checking only `default`
        would be a guard narrower than the thing it guards — and the alias most
        likely to be added later is the one named for sessions.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _assert_caches_are_disposable()
        self.assertIn('sessions', str(ctx.exception))

    @override_settings(CACHES={'default': {'BACKEND': _REDIS, 'LOCATION': 'redis://x'}})
    def test_the_message_says_what_to_do(self):
        """
        A refusal that does not say how to proceed gets worked around by
        deleting the refusal.
        """
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _assert_caches_are_disposable()
        message = str(ctx.exception)
        self.assertIn('REDIS_URL', message)
        self.assertIn('override_settings', message)


class TheTestSettingsForceADisposableCacheTests(SimpleTestCase):
    """
    The other half: assert the settings actually did it.

    This test runs under `manage.py test`, so the running configuration IS the
    thing being asserted — if the forcing block is removed, this fails on any
    machine with `REDIS_URL` set, which is the machine that matters.
    """

    def test_the_running_suite_is_on_a_disposable_cache(self):
        _assert_caches_are_disposable()

    def test_sessions_are_not_in_the_cache_during_tests(self):
        """
        The consequence, stated separately from the cause. If sessions ever go
        back to the cache backend under test, clearing between tests logs the
        test client out mid-test — which reads as a flaky authorisation bug.
        """
        from django.conf import settings

        self.assertNotEqual(
            settings.SESSION_ENGINE,
            'django.contrib.sessions.backends.cache',
        )
