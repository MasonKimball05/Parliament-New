"""
Regression tests for the v3.18.7 middleware hot-path batch (08-06-26 review).

WHY A SEPARATE MODULE
---------------------
`test_query_budgets.py` measures *how many* queries the chain costs.
This module asserts the things a count cannot see:

* that a cached security control is still invalidated when it changes
  (`SystemLockdown`);
* that a blocking control applies on the paths it was silently skipping
  (`IPBlacklist` on `/admin/` and `/contact/submit/`);
* that the session fingerprint is **compared before it is overwritten**, an
  ordering property that produced no signal either way when it was wrong;
* that query counting works with `DEBUG=False`, which is the only setting
  production ever runs under.

Every one of these is a control that failed **silently** — no exception, no
log, no failing test, in three cases for months. So the assertions below are
deliberately about observable behaviour rather than about implementation:
"a blacklisted IP gets a 403 on /admin/", not "the gate is above the early
return". The second phrasing passes for the wrong reason as soon as someone
rearranges the file.

✅ EXECUTED 08-11-26 (the note below stood from 08-06 to 08-10 and is kept as
history). Django runs in the review sandbox now: `pip install -r
requirements.txt`, then `DB_BACKEND=sqlite REDIS_URL= DJANGO_DEBUG=False`.
The original note read: *"NOT YET EXECUTED — written in an environment with no
working Django (the repo `.venv` is a macOS interpreter)."*

⚠️ AND WHEN IT WAS FINALLY RUN, TWO TESTS IN THIS MODULE COULD NOT PASS — not
because the middleware was broken but because **v3.19.3 made the thing they
assert probabilistic.** Both `PerformanceQueryCountingTests.test_the_middleware_
records_a_nonzero_count_for_a_real_request` and `MonitoringReaderLivenessTests.
test_the_debug_endpoint_returns_real_numbers` make ONE request and then assert a
stored metric exists for it; `_append_metric` opens with
`if random.randrange(SAMPLE_ONE_IN): return`, so one request is stored with
probability 1 in 20 and both tests failed ~95 % of runs. The 08-10 batch ran
them, saw them red, and recorded the cause as "cache-throttle timing; may be
LocMem-vs-Redis". It was neither.

**The rule, v3.19.7, and it is a narrower cousin of this repo's threshold rule:
when you make an operation probabilistic, every test asserting that the
operation happened becomes a coin flip — and a coin flip that usually loses is
indistinguishable from a broken feature.** Both tests now pin `SAMPLE_ONE_IN`
to 1 for the duration of the request, which is the honest fix: they are about
whether the middleware RECORDS, not about whether it SAMPLES. The sampling
behaviour has its own tests elsewhere in this module, and the two liveness tests
additionally assert `total_requests`, the exact counter v3.19.3 introduced
precisely so that sampling could not make the totals lie.
"""

from datetime import timedelta          # v3.19.5 — EveryThresholdHasBothAnswers
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone  # v3.19.3 — PerformanceMetricWriteCostTests

from src.models import IPBlacklist, ParliamentUser, SystemLockdown, UserSession
from src.models_feature_flags import SiteSetting

CHROME_MAC = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
FIREFOX_WINDOWS = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) '
    'Gecko/20100101 Firefox/121.0'
)


def make_user(uid='hotpath-user', **kwargs):
    defaults = dict(
        name='Hot Path User', username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('hot-path-test-pass-12345!')
    user.save()
    return user


# ---------------------------------------------------------------------------
# Finding 1 — SystemLockdown singleton
# ---------------------------------------------------------------------------

class SystemLockdownCacheTests(TestCase):
    """
    `get_instance()` is read by `EmergencyLockdownMiddleware` on essentially
    every request. v3.18.7 cached it — which is only safe if the invalidation
    is airtight, because the failure mode of a stale entry is **an emergency
    control that does not take effect**.
    """

    def setUp(self):
        cache.clear()

    def test_repeated_reads_cost_one_query(self):
        SystemLockdown.get_instance()  # prime
        with CaptureQueriesContext(connection) as captured:
            SystemLockdown.get_instance()
            SystemLockdown.get_instance()
            SystemLockdown.get_instance()
        self.assertEqual(
            len(captured.captured_queries), 0,
            'Three warm `get_instance()` calls hit the database '
            f'{len(captured.captured_queries)}×; the cache is not being read.',
        )

    def test_reading_the_lockdown_state_never_writes(self):
        """
        ⚠️ v3.19.10 — THE READ PATH USED TO BE A WRITE PATH.

        `get_instance()` was `get_or_create(pk=1)`, and this method is called by
        `EmergencyLockdownMiddleware` on essentially every request. So the first
        request after a fresh install, a database restore, or an admin deleting
        the row issued an INSERT *while serving a GET*, and concurrent first
        requests raced for it. v3.18.7 narrowed that race by caching; narrowing
        a race is not the same as not having one.

        It also showed up somewhere nobody was looking: every ceiling in
        `test_query_budgets.py` was exactly three queries too high (SAVEPOINT +
        INSERT + RELEASE), because each `TestCase` rolls back and the first
        request in every test paid to create the row again.

        Asserted as **no write of any kind**, not "no INSERT", because the
        interesting property is that answering the question does not change the
        answer.
        """
        SystemLockdown.objects.all().delete()
        cache.clear()

        with CaptureQueriesContext(connection) as captured:
            instance = SystemLockdown.get_instance()

        writes = [
            q['sql'] for q in captured.captured_queries
            if q['sql'].lstrip()[:6].upper() in ('INSERT', 'UPDATE', 'DELETE')
        ]
        self.assertEqual(
            writes, [],
            f'Reading the lockdown state wrote to the database: {writes}',
        )
        self.assertFalse(
            SystemLockdown.objects.exists(),
            'get_instance() created the singleton row as a side effect of a read',
        )
        self.assertFalse(
            instance.is_active,
            'With no row configured the system must not be in lockdown — the '
            'placeholder has to fail OPEN here, which is what get_or_create '
            'also did.',
        )

    def test_a_missing_row_is_cached_so_it_is_not_re_queried(self):
        """
        ⚠️ THE HALF THAT IS EASY TO DROP, and dropping it would have been a
        performance regression on the widest hot path in the app.

        Removing the write without caching the miss trades one INSERT-once for
        **one uncached SELECT on every request, forever**, on any install where
        nobody has opened the lockdown page — undoing v3.18.7 on exactly the
        path v3.18.7 existed for. `cache.get` returns `None` for a miss, so the
        absence has to be stored as a sentinel rather than as `None`.
        """
        SystemLockdown.objects.all().delete()
        cache.clear()

        SystemLockdown.get_instance()  # prime
        with CaptureQueriesContext(connection) as captured:
            SystemLockdown.get_instance()
            SystemLockdown.get_instance()

        self.assertEqual(
            len(captured.captured_queries), 0,
            'Two warm reads with no row present cost '
            f'{len(captured.captured_queries)} queries; the absence is not '
            'being cached, so every request re-asks.',
        )

    def test_creating_the_row_invalidates_the_cached_absence(self):
        """
        The sentinel is only safe because the `post_save` receiver fires when
        the row is **created** — the exact moment "there is no row" stops being
        true. If that ever stops holding, a lockdown activated on a fresh
        install would not engage, which is the same failure this class's main
        test guards from the other direction.
        """
        SystemLockdown.objects.all().delete()
        cache.clear()

        self.assertFalse(SystemLockdown.get_instance().is_active)  # caches the miss

        admin = make_user('lockdown-fresh-admin', is_admin=True)
        SystemLockdown.get_instance().activate(admin, reason='fresh install drill')

        self.assertEqual(
            SystemLockdown.objects.count(), 1,
            'Activating from a placeholder must write the singleton, not a '
            'second row — hence pk=1 on the placeholder.',
        )
        self.assertTrue(
            SystemLockdown.get_instance().is_active,
            'The cached absence outlived the row being created, so an '
            'emergency control did not take effect.',
        )

    def test_the_control_an_existing_row_is_still_returned(self):
        """
        Without this, a `get_instance()` that always returned a blank
        placeholder would pass every test above — and would mean lockdown could
        never be in effect at all.
        """
        SystemLockdown.objects.all().delete()
        cache.clear()
        SystemLockdown.objects.create(pk=1, reason='pre-existing')

        instance = SystemLockdown.get_instance()
        self.assertEqual(instance.pk, 1)
        self.assertEqual(instance.reason, 'pre-existing')

    def test_activating_lockdown_takes_effect_on_the_very_next_read(self):
        """
        THE test in this class. A TTL alone would make this pass only after
        five minutes, and "the lockdown engages within five minutes" is not
        what the word emergency means.
        """
        instance = SystemLockdown.get_instance()
        self.assertFalse(instance.is_active)

        admin = make_user('lockdown-admin', is_admin=True)
        SystemLockdown.get_instance().activate(admin, reason='drill')

        self.assertTrue(
            SystemLockdown.get_instance().is_active,
            'Lockdown was activated but the next read still reported it '
            'inactive — the post_save receiver did not fire, so the control '
            'fails OPEN for the length of the TTL.',
        )

    def test_deactivating_lockdown_takes_effect_on_the_very_next_read(self):
        """The other direction: a stale ACTIVE entry locks everyone out."""
        admin = make_user('lockdown-admin-2', is_admin=True)
        SystemLockdown.get_instance().activate(admin, reason='drill')
        self.assertTrue(SystemLockdown.get_instance().is_active)

        SystemLockdown.get_instance().deactivate(admin)
        self.assertFalse(
            SystemLockdown.get_instance().is_active,
            'Lockdown was deactivated but the next read still reported it '
            'active — members stay locked out until the TTL expires.',
        )

    def test_a_plain_save_also_invalidates(self):
        """
        ⚠️ THIS IS WHY INVALIDATION IS A SIGNAL AND NOT A `cache.delete` INSIDE
        activate()/deactivate().

        `SystemLockdownAdmin` lets an admin edit `is_active`, `whitelisted_ips`
        and `message` directly on the changeform, and that path calls `save()`
        without going near `activate()`. An implementation that busted the
        cache in those two methods would pass every other test in this class
        and fail this one.
        """
        instance = SystemLockdown.get_instance()
        instance.is_active = True
        instance.save()

        self.assertTrue(
            SystemLockdown.get_instance().is_active,
            'A direct save() did not invalidate the cache, so editing the '
            'lockdown from /admin/ has no effect until the TTL expires.',
        )

    def test_whitelist_edits_are_visible_immediately(self):
        """A stale whitelist locks out the admin trying to fix the lockdown."""
        instance = SystemLockdown.get_instance()
        instance.whitelisted_ips = ['203.0.113.7']
        instance.save()

        refreshed = SystemLockdown.get_instance()
        self.assertTrue(refreshed.is_ip_whitelisted('203.0.113.7'))
        self.assertFalse(refreshed.is_ip_whitelisted('203.0.113.8'))


# ---------------------------------------------------------------------------
# Finding 2 — IPBlacklist path coverage
# ---------------------------------------------------------------------------

@override_settings(BEHIND_CLOUDFLARE=False)
class IPBlacklistPathCoverageTests(TestCase):
    """
    Before v3.18.7 the blacklist gate sat below the pattern-scan early return,
    so eight paths — including `/admin/` and the public `/contact/submit/` —
    were never checked, under a comment claiming the gate applied to "all
    requests".

    The honeypot auto-blacklists scanners *specifically* so this gate blocks
    them (`view/honeypot.py:120`), and `/admin/` is where scanners go next.
    """

    BLOCKED_IP = '198.51.100.44'
    CLEAN_IP = '198.51.100.45'

    def setUp(self):
        cache.clear()
        IPBlacklist.objects.create(
            ip_address=self.BLOCKED_IP, reason='test', is_active=True,
        )
        self.client = Client()

    def _get(self, path, ip):
        return self.client.get(path, REMOTE_ADDR=ip)

    def test_a_blacklisted_ip_is_blocked_on_admin(self):
        response = self._get('/admin/', self.BLOCKED_IP)
        self.assertEqual(
            response.status_code, 403,
            'A blacklisted IP reached /admin/. This is the path scanners probe '
            'and the reason the honeypot writes blacklist rows at all.',
        )

    def test_a_blacklisted_ip_is_blocked_on_the_public_contact_form(self):
        response = self._get('/contact/submit/', self.BLOCKED_IP)
        self.assertEqual(
            response.status_code, 403,
            'A blacklisted IP reached /contact/submit/ — public, unauthenticated '
            'and csrf_exempt by design, i.e. exactly what you blacklist an '
            'abuser for.',
        )

    def test_a_blacklisted_ip_is_blocked_on_an_ordinary_path(self):
        """The case that already worked. Here so a regression is attributable."""
        response = self._get(reverse('landing'), self.BLOCKED_IP)
        self.assertEqual(response.status_code, 403)

    def test_a_clean_ip_is_not_blocked_on_those_paths(self):
        """
        THE NEGATIVE CONTROL, and it is load-bearing: without it every
        assertion above would pass against a middleware that 403s everyone.
        """
        for path in ('/admin/', '/contact/submit/', reverse('landing')):
            with self.subTest(path=path):
                response = self._get(path, self.CLEAN_IP)
                self.assertNotEqual(
                    response.status_code, 403,
                    f'A non-blacklisted IP got 403 on {path} — the gate is '
                    f'blocking indiscriminately.',
                )

    def test_static_remains_exempt_from_the_blacklist_gate(self):
        """
        Deliberate: nginx serves /static/ in production so Django never sees
        it, and the exemption is a dev-time cost saving. Asserted so that
        someone tightening the list later has to do it on purpose.
        """
        response = self._get('/static/images/am-coat-of-arms.png', self.BLOCKED_IP)
        self.assertNotEqual(response.status_code, 403)

    def test_an_inactive_blacklist_row_does_not_block(self):
        IPBlacklist.objects.filter(ip_address=self.BLOCKED_IP).update(is_active=False)
        cache.clear()  # the gate caches for 5 minutes on both outcomes
        response = self._get('/admin/', self.BLOCKED_IP)
        self.assertNotEqual(response.status_code, 403)

    def test_pattern_scanning_is_still_skipped_on_the_scan_exempt_paths(self):
        """
        The other half of the v3.18.7 change, and the one most likely to have
        broken by accident: splitting one list into two must not start scanning
        the rich-HTML editor and the free-text contact form, which is what the
        skip list existed for in the first place.

        A CSS-semicolon payload of the kind that trips the SQLi patterns should
        pass through `/officers/edit-landing-page/` untouched.
        """
        from src.middleware.security import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(lambda request: None)
        self.assertIn('/officers/edit-landing-page/', middleware.skip_scan_paths)
        self.assertIn('/contact/submit/', middleware.skip_scan_paths)
        self.assertIn('/admin/', middleware.skip_scan_paths)
        # …and those paths are NOT exempt from the blacklist gate.
        self.assertNotIn('/admin/', middleware.blacklist_exempt_paths)
        self.assertNotIn('/contact/submit/', middleware.blacklist_exempt_paths)

    def test_the_two_path_lists_are_not_the_same_object(self):
        """
        Guards the regression directly: if a future edit collapses these back
        into one list, /admin/ silently stops being checked again and nothing
        else in this file would necessarily notice.
        """
        from src.middleware.security import InputSanitizationMiddleware

        middleware = InputSanitizationMiddleware(lambda request: None)
        self.assertIsNot(
            middleware.skip_scan_paths, middleware.blacklist_exempt_paths,
            'The scan-skip list and the blacklist-exempt list are the same '
            'object. They govern different controls and must stay separate — '
            'merging them is the v3.18.7 bug.',
        )


# ---------------------------------------------------------------------------
# Finding 3 — session fingerprint ordering
# ---------------------------------------------------------------------------

class SessionFingerprintOrderingTests(TestCase):
    """
    THE INVARIANT: the stored fingerprint is never rewritten without first
    being compared against the request doing the rewriting.

    Before v3.18.7 the record was rewritten every 300 s and compared every
    600 s, so a hijack first arriving in the back half of a cycle overwrote the
    baseline unexamined and was then compared against itself. Never detected —
    not late, at all.
    """

    def setUp(self):
        cache.clear()
        self.user = make_user('fingerprint-user')
        self.client = Client()
        self.client.force_login(self.user)

    def _request_as(self, user_agent):
        return self.client.get(reverse('home'), HTTP_USER_AGENT=user_agent)

    def _expire_throttle(self):
        """Simulate the throttle window elapsing."""
        session_key = self.client.session.session_key
        cache.delete(f'session_check_{session_key}')

    def test_a_changed_user_agent_is_detected_after_the_throttle_expires(self):
        self._request_as(CHROME_MAC)
        self._expire_throttle()

        with self.assertLogs('function_calls', level='WARNING') as logs:
            self._request_as(FIREFOX_WINDOWS)

        self.assertTrue(
            any('SESSION FINGERPRINT' in line for line in logs.output),
            f'A browser+OS change went undetected. Log output was:\n'
            f'{logs.output}',
        )

    def test_an_unchanged_user_agent_is_not_flagged(self):
        """
        NEGATIVE CONTROL. Without it, a middleware that warned on every request
        would pass the test above.
        """
        self._request_as(CHROME_MAC)
        self._expire_throttle()

        with patch('src.middleware.session_tracking.logger') as mock_logger:
            self._request_as(CHROME_MAC)

        warnings = [str(c) for c in mock_logger.warning.call_args_list]
        self.assertFalse(
            any('SESSION FINGERPRINT' in w for w in warnings),
            f'The same browser on the same OS was flagged as suspicious: {warnings}',
        )

    def test_detection_survives_several_throttle_cycles(self):
        """
        ⚠️ THE REGRESSION TEST FOR THE ACTUAL BUG, and it is the one that fails
        against the pre-v3.18.7 tree.

        Under the old two-throttle design the record was rewritten on the cycle
        *between* comparisons, so by the time a comparison ran the baseline was
        already the attacker's. Cycling several times before the changed
        user-agent arrives reproduces exactly that: with one throttle the
        baseline is always the last *compared* value, so detection still fires
        no matter how many cycles have passed.
        """
        self._request_as(CHROME_MAC)
        for _ in range(3):
            self._expire_throttle()
            self._request_as(CHROME_MAC)

        self._expire_throttle()
        with self.assertLogs('function_calls', level='WARNING') as logs:
            self._request_as(FIREFOX_WINDOWS)

        self.assertTrue(
            any('SESSION FINGERPRINT' in line for line in logs.output),
            'A user-agent change went undetected after several throttle '
            'cycles. This is the two-throttle bug: the baseline is being '
            'refreshed without being compared.',
        )

    def test_the_stored_record_is_updated_after_the_comparison(self):
        """
        The write must still happen — a fix that only reordered the reads and
        dropped the update would pass every assertion above while quietly
        breaking the Active Sessions panel.
        """
        self._request_as(CHROME_MAC)
        stored = UserSession.objects.get(session_key=self.client.session.session_key)
        self.assertEqual(stored.browser, 'Chrome')

        self._expire_throttle()
        self._request_as(FIREFOX_WINDOWS)

        stored.refresh_from_db()
        self.assertEqual(
            stored.browser, 'Firefox',
            'The session record was not updated after the comparison — the '
            'compare-then-write ordering dropped the write.',
        )

    def test_the_throttle_suppresses_work_within_the_window(self):
        """The throttle still throttles; this is not a per-request DB write."""
        self._request_as(CHROME_MAC)
        with CaptureQueriesContext(connection) as captured:
            self._request_as(CHROME_MAC)
        session_writes = [
            q['sql'] for q in captured.captured_queries
            if 'usersession' in q['sql'].lower() and (
                'update' in q['sql'].lower() or 'insert' in q['sql'].lower())
        ]
        self.assertEqual(
            session_writes, [],
            f'The session record was written inside the throttle window: '
            f'{session_writes[:2]}',
        )


# ---------------------------------------------------------------------------
# Finding 4 — query counting under DEBUG=False
# ---------------------------------------------------------------------------

class PerformanceQueryCountingTests(TestCase):
    """
    `PerformanceMiddleware` counted queries with `len(connection.queries)`,
    which Django populates only when `force_debug_cursor or settings.DEBUG`.
    Production runs DEBUG=False, so the count was **always 0** — and the
    slow-request alarm reported `(0 queries, 0ms DB time)` on every slow page,
    pointing whoever read it away from the database.
    """

    def test_the_counter_counts_with_debug_false(self):
        """
        ⚠️ `override_settings(DEBUG=False)` is the whole point of this test —
        the Django test runner forces DEBUG=False anyway, but stating it means
        the test still says what it is about if that ever changes.
        """
        from src.middleware.performance import _QueryCounter

        counter = _QueryCounter()
        with override_settings(DEBUG=False):
            with connection.execute_wrapper(counter):
                ParliamentUser.objects.count()
                ParliamentUser.objects.exists()

        self.assertGreaterEqual(
            counter.count, 2,
            f'The counter recorded {counter.count} queries for two ORM calls '
            f'with DEBUG=False. This is the exact bug: '
            f'`len(connection.queries)` reports 0 here.',
        )
        self.assertGreater(
            counter.total_ms, 0,
            'Query timing came back as zero; the counter is not timing.',
        )

    def test_len_connection_queries_would_have_reported_zero(self):
        """
        ⚠️ THE PROOF THAT THE TEST ABOVE IS MEANINGFUL, not just passing.

        Without this, `test_the_counter_counts_with_debug_false` would pass
        against a hypothetical fix that changed nothing, because the reader has
        no way to tell that the old mechanism really did report zero. Assert
        the broken behaviour directly.
        """
        with override_settings(DEBUG=False):
            connection.force_debug_cursor = False
            before = len(connection.queries)
            ParliamentUser.objects.count()
            after = len(connection.queries)

        self.assertEqual(
            after - before, 0,
            'connection.queries recorded a query with DEBUG=False, which '
            'contradicts the premise of this fix. If Django changed this, the '
            'middleware could go back to the simpler mechanism.',
        )

    def test_the_middleware_records_a_nonzero_count_for_a_real_request(self):
        """
        ⚠️ v3.19.7 — `SAMPLE_ONE_IN` is pinned to 1 here. Without it this test
        stores the request with probability 1/20 and fails 95 % of the time,
        which is what it did on every run from v3.19.3 onwards. The subject is
        whether the middleware counts queries at all — the sampling rate is a
        different question with its own tests, and letting it decide this one
        made a real guard look like a flaky one.
        """
        from src.middleware import performance
        from src.middleware.performance import _get_entries

        user = make_user('perf-user')
        cache.clear()
        client = Client()
        client.force_login(user)
        with patch.object(performance, 'SAMPLE_ONE_IN', 1):
            client.get(reverse('home'))

        entries = [e for e in _get_entries() if e[2] == reverse('home')]
        self.assertTrue(entries, 'No metric was recorded for the home page.')
        self.assertGreater(
            entries[-1][3], 0,
            'The home page was recorded as costing 0 queries. That is the '
            'number this release exists to stop reporting.',
        )

    def test_the_exact_request_counter_is_not_affected_by_sampling(self):
        """
        v3.19.7 — the liveness assertion that needs no pinning, and the reason
        `total_requests` was made an exact `cache.incr` in v3.19.3 rather than a
        `len()` of the buffer.

        This is the check to reach for first when asking "is the monitoring
        alive": it is true after one request, every time, whatever the sample
        rate is set to. A test that has to control the sample rate to observe
        the system is a test that will break again the next time the rate moves.
        """
        from src.middleware.performance import get_performance_summary

        user = make_user('perf-counter-user')
        cache.clear()
        client = Client()
        client.force_login(user)
        client.get(reverse('home'))

        self.assertGreater(
            get_performance_summary()['total_requests'], 0,
            'total_requests is an exact counter incremented on every request '
            'regardless of sampling. Zero here means the middleware is not '
            'running at all.',
        )


# ---------------------------------------------------------------------------
# Finding 7 — SiteSetting caching
# ---------------------------------------------------------------------------

class SiteSettingCacheTests(TestCase):
    """
    `SiteSetting.get_setting` is called by `Enforce2FAMiddleware` on every
    authenticated request and was an uncached `objects.get`, beside a
    `FeatureFlag` helper in the same module that has been cached since v3.17.1.
    """

    def setUp(self):
        cache.clear()
        SiteSetting.objects.create(
            key='2fa_policy_mode', display_name='2FA policy',
            setting_type='string', value='all', default_value='none',
        )

    def test_repeated_reads_cost_one_query(self):
        SiteSetting.get_setting('2fa_policy_mode', 'none')  # prime
        with CaptureQueriesContext(connection) as captured:
            for _ in range(3):
                SiteSetting.get_setting('2fa_policy_mode', 'none')
        self.assertEqual(len(captured.captured_queries), 0)

    def test_a_missing_key_is_also_cached(self):
        SiteSetting.get_setting('no-such-key', 'fallback')  # prime the miss
        with CaptureQueriesContext(connection) as captured:
            SiteSetting.get_setting('no-such-key', 'fallback')
        self.assertEqual(
            len(captured.captured_queries), 0,
            'A missing key re-queried on every call — the miss is not cached, '
            'which is the common case for an unseeded setting.',
        )

    def test_a_cached_miss_does_not_leak_one_callers_default_to_another(self):
        """
        ⚠️ THE SUBTLE ONE, and the reason the cache stores `found` rather than
        the value alone. Callers pass DIFFERENT defaults for the same key:
        caching a miss as its default would hand the first caller's fallback to
        the second.
        """
        self.assertEqual(SiteSetting.get_setting('unseeded', 'first'), 'first')
        self.assertEqual(
            SiteSetting.get_setting('unseeded', 'second'), 'second',
            "A cached miss returned the FIRST caller's default. The cache is "
            "storing the default instead of recording that the row was absent.",
        )
        self.assertIsNone(SiteSetting.get_setting('unseeded'))

    def test_set_setting_invalidates(self):
        self.assertEqual(SiteSetting.get_setting('2fa_policy_mode', 'none'), 'all')
        SiteSetting.set_setting('2fa_policy_mode', 'officers_and_admins')
        self.assertEqual(
            SiteSetting.get_setting('2fa_policy_mode', 'none'),
            'officers_and_admins',
            'A policy change did not take effect — this gates 2FA enforcement.',
        )

    def test_deleting_a_setting_invalidates(self):
        self.assertEqual(SiteSetting.get_setting('2fa_policy_mode', 'none'), 'all')
        SiteSetting.objects.filter(key='2fa_policy_mode').delete()
        self.assertEqual(
            SiteSetting.get_setting('2fa_policy_mode', 'none'), 'none',
            'A deleted setting kept answering from cache. Note queryset '
            '.delete() is what the admin changelist uses — the same bulk-path '
            'gap v3.17.3 fixed for FeatureFlag.',
        )

    def test_typed_values_survive_the_cache(self):
        SiteSetting.objects.create(
            key='chat_poll_interval', display_name='Poll interval',
            setting_type='integer', value='3000', default_value='5000',
        )
        first = SiteSetting.get_setting('chat_poll_interval', 5000)
        second = SiteSetting.get_setting('chat_poll_interval', 5000)
        self.assertEqual(first, 3000)
        self.assertEqual(
            second, 3000,
            'The cached read returned a different type or value than the '
            'uncached one — get_value() typing must happen before the cache '
            'write, not after.',
        )
        self.assertIsInstance(second, int)


# ---------------------------------------------------------------------------
# Finding 4(a)/(c) — the dead readers
# ---------------------------------------------------------------------------

class MonitoringReaderLivenessTests(TestCase):
    """
    Both dead readers failed silently for months: one read a cache key nothing
    wrote, the other imported a name that had been deleted. Neither raised
    anything a human saw.
    """

    def test_the_debug_endpoint_returns_real_numbers(self):
        """
        ⚠️ v3.19.7 — `SAMPLE_ONE_IN` pinned to 1 for the metric-generating
        request; see the module docstring. `recent_metrics_count` counts STORED
        samples, so under 1-in-20 sampling a single request leaves it at zero
        95 % of the time — and this test's own failure message says the endpoint
        "is reading the wrong cache key again", which is a confident diagnosis
        of the wrong thing.
        """
        from src.middleware import performance

        admin = make_user('debug-admin', is_admin=True)
        cache.clear()
        client = Client()
        client.force_login(admin)
        with patch.object(performance, 'SAMPLE_ONE_IN', 1):
            client.get(reverse('home'))  # generate at least one metric

        response = client.get(reverse('debug_performance_metrics'))
        self.assertEqual(response.status_code, 200)
        payload = response.json()

        self.assertGreater(
            payload['recent_metrics_count'], 0,
            'The debug endpoint reported zero recorded requests immediately '
            'after a request was made — it is reading the wrong cache key '
            'again, which is what it did for six months.',
        )
        self.assertGreater(payload['avg_response_time_ms'], 0)

    def test_memory_report_runs_without_an_import_error(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command('memory_report', stdout=out, stderr=StringIO())
        output = out.getvalue()

        self.assertNotIn(
            '_performance_metrics', output,
            'memory_report still references the dict deleted in April 2026.',
        )
        self.assertNotIn(
            'cannot import name', output,
            f'memory_report printed an import error instead of stats:\n{output}',
        )

    def test_no_view_reads_the_dead_perf_cache_key(self):
        """
        Cheap, permanent guard against the exact shape of the bug: a reader
        pointed at a key nothing writes. `perf_metrics_recent` was never
        written by anything, at any point in the repo's history.

        ⚠️ THIS TEST WAS WRONG ON ITS FIRST RUN, AND THE FIX IS THE POINT.
        The first draft grepped each file for the literal string
        `perf_metrics_recent` — and failed on `view/debug_panel.py`, whose
        docstring *names the key while explaining the bug*. The code no longer
        reads it; the prose mentions it. A substring search cannot tell those
        apart, which makes it a test that fails on documentation and would pass
        on a read spelled any other way.

        That is this codebase's own rule from v3.18.4 — **an assertion that
        cannot distinguish the bug from the fixture is not an assertion** — so
        it now parses instead of greps: walk the AST and collect string
        literals in the FIRST ARGUMENT POSITION of a `cache.get`/`cache.set`
        call. Comments and docstrings are invisible to that by construction,
        which is exactly the distinction that was missing.

        The general version — collect every literal cache key read anywhere in
        `src/` and assert each has a writer — is the deferred idea 12 from the
        08-06 review. It is strictly better and it is not this test; scoping
        this one to the known-dead key keeps it honest about what it proves.
        """
        import ast
        import pathlib

        src_root = pathlib.Path(__file__).resolve().parent.parent.parent
        this_file = pathlib.Path(__file__).name
        offenders = []

        for path in src_root.rglob('*.py'):
            if path.name == this_file:
                continue
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except SyntaxError:  # pragma: no cover - not our problem to police
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                func = node.func
                if not isinstance(func, ast.Attribute):
                    continue
                if func.attr not in ('get', 'set', 'get_or_set', 'delete'):
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value == 'perf_metrics_recent':
                    offenders.append(f'{path.relative_to(src_root)}:{node.lineno}')

        self.assertEqual(
            offenders, [],
            f'`perf_metrics_recent` is used as a cache key at {offenders} and '
            f'is written nowhere in the repo — this is the six-month bug '
            f'returning. Note this checks CALL SITES, not mentions: a docstring '
            f'naming the key is fine and deliberate.',
        )


# ---------------------------------------------------------------------------
# v3.19.3 — the chain's CACHE cost
# ---------------------------------------------------------------------------


class PerformanceMetricWriteCostTests(TestCase):
    """
    `PerformanceMiddleware`'s own bookkeeping, measured as bytes and round
    trips rather than as queries.

    ⚠️ WHY THIS CLASS EXISTS, AND IT IS THE SAME LESSON ONE RESOURCE OVER.
    v3.18.7 added `MiddlewareChainQueryBudgetTests` in direct response to the
    08-06 finding that middleware cost hides inside every per-view ceiling and
    reads as the floor. That was right, and it counts QUERIES — so it was
    structurally unable to see that the single most expensive per-request
    operation in the chain was a CACHE write: `_append_metric` did a
    read-modify-write of the entire 500-entry history on every request,
    measured at ~0.25 ms of CPU and ~19 KB of Redis traffic in two serialised
    round trips, on every page, every /media/ download and every 404.

    CLAUDE.md already records this shape twice — v3.18.5's miss was a *column*
    rather than a call site, which forced the generalisation once. A *resource*
    is the next widening: **a per-request budget has to name what it measures,
    and "queries" was never the only answer.**

    These assertions are about the write pattern, not about a byte count on a
    particular Redis build, because a byte ceiling would be measuring the
    compressor.
    """

    def setUp(self):
        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        cache.delete(performance.COUNT_KEY)
        cache.delete(performance.SAMPLED_KEY)

    def test_an_ordinary_request_usually_writes_no_history(self):
        """
        The property the fix is for: a fast request must NOT re-serialise the
        buffer. Sampled at 1-in-N, so this is asserted by forcing the sampler
        rather than by running many requests and hoping — a flaky assertion
        about a random draw is worse than no assertion.

        **This fails against the pre-v3.19.3 tree**, where every call wrote.
        """
        from src.middleware import performance

        with patch.object(performance.random, 'randrange', return_value=1):
            with patch.object(performance.cache, 'set') as mock_set:
                performance._append_metric(
                    (timezone.now(), 12.0, '/home/', 3, 4.0)
                )

        history_writes = [
            c for c in mock_set.call_args_list
            if c.args and c.args[0] == performance.CACHE_KEY
        ]
        self.assertEqual(
            history_writes, [],
            'A fast, unsampled request re-serialised the metrics buffer. That '
            'is the ~19 KB / 2-round-trip cost this release removed.',
        )

    def test_a_slow_request_is_always_stored(self):
        """
        The other half, and the one that makes sampling safe to do at all: the
        data every N+1 hunt in this codebase has actually used is the slow
        list, and it must be complete. Forcing the sampler to say "skip" proves
        the slow path bypasses it rather than merely usually winning.
        """
        from src.middleware import performance

        with patch.object(performance.random, 'randrange', return_value=1):
            performance._append_metric(
                (timezone.now(), performance.ALWAYS_STORE_ABOVE_MS + 1, '/slow/', 40, 900.0)
            )

        stored = performance._get_entries()
        self.assertEqual(
            [e[2] for e in stored], ['/slow/'],
            'A slow request was sampled away. Slow requests must be stored '
            'unconditionally — sampling them would break the only use this '
            'data has ever been put to.',
        )

    def test_the_total_count_is_exact_and_not_sampled(self):
        """
        Sampling must not make the totals lies. `total_requests` comes from
        `cache.incr` now, so it counts every request including the ones whose
        timings were discarded — and is *more* accurate than before, when it
        was `len(entries)` and therefore pinned at MAX_STORED under any real
        traffic.
        """
        from src.middleware import performance

        with patch.object(performance.random, 'randrange', return_value=1):
            for _ in range(50):
                performance._append_metric(
                    (timezone.now(), 5.0, '/home/', 2, 1.0)
                )

        summary = performance.get_performance_summary()
        self.assertEqual(
            summary['total_requests'], 50,
            'Every request must be counted even when its timing is not stored.',
        )
        self.assertEqual(
            summary['stored_samples'], 0,
            'None of those should have been stored — randrange was forced to skip.',
        )
        self.assertTrue(
            summary['sampled'],
            'The summary must declare that its averages are sampled, or a '
            'caller will render an estimate as a measurement.',
        )

    def test_the_write_is_bounded_when_it_does_happen(self):
        """
        Sampling reduces how OFTEN the buffer is rewritten; it does not change
        the fact that a rewrite is O(MAX_STORED). Pinning the bound here so a
        future MAX_STORED increase is a visible decision rather than a silent
        multiplication of the cost that remains.
        """
        from src.middleware import performance

        with patch.object(performance.random, 'randrange', return_value=0):
            for i in range(performance.MAX_STORED + 25):
                performance._append_metric(
                    (timezone.now(), 5.0, f'/p{i}/', 2, 1.0)
                )

        self.assertEqual(
            len(performance._get_entries()), performance.MAX_STORED,
            'The buffer must stay capped at MAX_STORED.',
        )


class EveryCounterThisModuleWritesIsAlsoRead(TestCase):
    """
    v3.19.4 — the `perf_sampled_count` class of bug, asserted rather than
    remembered.

    ⚠️ WHAT WENT WRONG, because the shape is the point. v3.19.3 correctly
    identified that `total_requests` was a **saturating `len()`** capped at
    `MAX_STORED`, and replaced it with an exact `cache.incr` counter. In the
    same function it introduced `stored_samples` — another saturating `len()`,
    with the same defect, sixty lines from the fix — **while incrementing
    `SAMPLED_KEY` on every stored write and never reading the value.**

    One writer, no readers. It cost a Redis round trip per stored sample and
    held exactly the number `stored_samples` was being mistaken for.

    `MonitoringReaderLivenessTests.test_no_view_reads_the_dead_perf_cache_key`
    (v3.18.7) greps for readers of a *named* key and could not see this: a key
    with writers and no readers at all is a different shape from a key whose
    reader was deleted. **The guard was written against the instance; this one
    is written against the property**, which is the fourth time this codebase
    has had to make that generalisation.
    """

    def test_the_sampled_counter_reaches_the_summary(self):
        """
        The regression test proper: bump the counter and require the summary to
        show it. Fails against the v3.19.3 tree, where the value was written
        and discarded.
        """
        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        cache.delete(performance.COUNT_KEY)
        cache.delete(performance.SAMPLED_KEY)

        with patch.object(performance.random, 'randrange', return_value=0):
            for _ in range(7):
                performance._append_metric((timezone.now(), 5.0, '/home/', 2, 1.0))
        with patch.object(performance.random, 'randrange', return_value=1):
            for _ in range(93):
                performance._append_metric((timezone.now(), 5.0, '/home/', 2, 1.0))

        summary = performance.get_performance_summary()

        self.assertEqual(summary['total_requests'], 100, 'Exact: every request.')
        self.assertEqual(
            summary['sampled_requests'], 7,
            'Exact: every request that was STORED. This is the key that was '
            'written and never read.',
        )
        self.assertEqual(summary['stored_samples'], 7, 'Buffer occupancy.')

    def test_stored_samples_saturates_and_sampled_requests_does_not(self):
        """
        The distinction the names now carry, pinned so a future refactor cannot
        quietly collapse them back together.

        `stored_samples` is buffer occupancy and is *supposed* to stop at
        `MAX_STORED` — that is not a defect, it is what a ring buffer does.
        `sampled_requests` is a count of events and must keep going. Reporting
        the first where the second is meant is the whole bug.
        """
        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        cache.delete(performance.COUNT_KEY)
        cache.delete(performance.SAMPLED_KEY)

        overshoot = performance.MAX_STORED + 40
        with patch.object(performance.random, 'randrange', return_value=0):
            for i in range(overshoot):
                performance._append_metric((timezone.now(), 5.0, f'/p{i}/', 2, 1.0))

        summary = performance.get_performance_summary()

        self.assertEqual(
            summary['stored_samples'], performance.MAX_STORED,
            'Occupancy saturates. Correct, and the reason it must not be used '
            'as a count.',
        )
        self.assertEqual(
            summary['sampled_requests'], overshoot,
            'The counter must not saturate — it is the honest denominator for '
            'every average in the dict.',
        )

    def test_the_summary_does_not_call_a_buffer_count_a_request_count(self):
        """
        v3.19.4 renamed `requests_last_hour`/`requests_last_5min` to
        `samples_last_hour`/`samples_last_5min`.

        This is a naming assertion and it is worth having as a test rather than
        as a comment, because the old names were *documented in two different
        and mutually contradictory ways in the same file* — one docstring said
        they came from `cache.incr` counters and were exact, an inline comment
        four lines away said they were sample counts scaled to estimate real
        traffic, and the code did neither. The name is now the documentation.
        """
        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        cache.delete(performance.COUNT_KEY)
        cache.delete(performance.SAMPLED_KEY)

        for summary in (
            performance.get_performance_summary(),          # the `empty` path
            self._summary_with_one_entry(),                 # the populated path
        ):
            self.assertIn('samples_last_hour', summary)
            self.assertIn('samples_last_5min', summary)
            self.assertNotIn(
                'requests_last_hour', summary,
                'A buffer count must not be named as though it were traffic. '
                'If a reader needs estimated traffic, sample `total_requests` '
                'at two points in time.',
            )
            self.assertNotIn('requests_last_5min', summary)

    def _summary_with_one_entry(self):
        from src.middleware import performance

        with patch.object(performance.random, 'randrange', return_value=0):
            performance._append_metric((timezone.now(), 5.0, '/home/', 2, 1.0))
        return performance.get_performance_summary()

    def test_every_reader_of_the_summary_asks_for_a_key_it_returns(self):
        """
        The rename's blast radius, checked mechanically rather than by grep at
        review time.

        Reads the source of every module known to consume
        `get_performance_summary()` and asserts that each `summary['…']` /
        `summary.get('…')` subscript names a key the function actually returns.
        A missing key is a `KeyError` on the admin dashboard or a silent `None`
        in a JSON response, and neither shows up until someone opens the page.
        """
        import re
        from pathlib import Path

        from django.conf import settings

        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        valid = set(performance.get_performance_summary().keys())

        readers = [
            Path(settings.BASE_DIR) / 'src' / 'view' / 'debug_panel.py',
            Path(settings.BASE_DIR) / 'src' / 'management' / 'commands' / 'memory_report.py',
        ]
        # `[a-z0-9_]`, not `[a-z_]` — `samples_last_5min` has a digit in it, and
        # a key-name regex that cannot match every key it is checking silently
        # checks fewer of them than it claims to.
        pattern = re.compile(r"""summary(?:\.get\(|\[)\s*['"]([a-z0-9_]+)['"]""")

        for path in readers:
            if not path.exists():                 # pragma: no cover
                continue
            for key in set(pattern.findall(path.read_text())):
                self.assertIn(
                    key, valid,
                    f'{path.name} reads summary["{key}"], which '
                    f'get_performance_summary() does not return. '
                    f'Available: {sorted(valid)}',
                )


class EveryThresholdHasBothAnswers(TestCase):
    """
    ⚠️ v3.19.5 — A THRESHOLD MUST BE CROSSABLE **AND** UNCROSSABLE, and this one
    has now been wrong three releases running in both directions.

    `memory_report`'s "the performance buffer is costing too much" recommendation:

      * v3.18.7 and earlier — `total_requests > MAX_STORED * 0.8`. Always true
        once `total_requests` became an unbounded counter.
      * v3.19.3 — swapped to `stored_samples > MAX_STORED * 0.8`. A correct
        observation about the wrong quantity: a full ring buffer is the *steady
        state* of a ring buffer trimmed on every write. Also always true.
      * v3.19.4 — spotted that, wrote down the right rule (*before writing a
        threshold, ask what the world looks like when it is NOT crossed*), and
        replaced it with `buffer_bytes > 512 * 1024`. **Never** true: roughly 38x
        a completely full buffer.

    Two releases were spent swapping the variable inside a condition whose
    *shape* was the bug, and then one more overshooting in the other direction.
    The rule needed its mirror — **also ask what the world looks like when it IS
    crossed** — and a check that cannot fire is worse than no check, because it
    reads as coverage.

    These tests assert the property directly: build the world where it should
    fire, build the world where it should not, and require the answers to differ.
    A test that only did the first half is what every previous version would have
    passed.

    ⚠️ EVERY FIXTURE HERE USES DISTINCT PATHS AND DISTINCT TIMESTAMPS, AND THAT
    IS THE MOST IMPORTANT LINE IN THIS CLASS. `pickle` memoises repeated objects,
    so a buffer of 500 *identical* entries serialises the timestamp and the path
    once and back-references them 499 times — 13,577 bytes against 22-33 KB for
    the same number of real requests. That is almost certainly how the 13,693
    figure quoted in `_append_metric` was produced, and it is 1.6-2.5x too small.
    A fixture whose rows are identical does not measure serialisation, because
    every serialiser worth using deduplicates. Do not "simplify" these builders
    into a list multiplication.
    """

    def setUp(self):
        from src.middleware import performance

        cache.delete(performance.CACHE_KEY)
        self.addCleanup(cache.delete, performance.CACHE_KEY)

    def _entries(self, count, path_for):
        """
        `count` entries of the real tuple shape.

        `path_for` is called with the index, so every entry can carry a distinct
        path — see the class docstring for why that is not optional. The
        timestamp and duration vary for the same reason.
        """
        now = timezone.now()
        return [
            (now + timedelta(seconds=i), 12.5 + i * 0.01, path_for(i), 3, 4.2)
            for i in range(count)
        ]

    #: What a busy chapter's buffer actually looks like: a handful of routes,
    #: some carrying an id. Measured at 44-67 B/entry.
    ORDINARY_ROUTES = [
        '/legislation/history/', '/vote/', '/home/', '/calendar/', '/profile/',
        '/admin-v2/dashboard/', '/service-hours/dashboard/',
        '/committee/3/vote/', '/search/',
    ]
    # v3.19.7 — `/global-search/` was in this list and is not a route; the
    # global search page is `/search/` (url name `global_search`). Caught by
    # `test_hardcoded_urls`, which had been red on it since the fixture was
    # written. Harmless here — these strings are only measured for their BYTE
    # LENGTH, and the two differ by seven — but the fixture's stated purpose is
    # "ordinary routes this application actually serves", and a made-up path
    # quietly makes it a fixture about nothing. Fixed rather than exempted: the
    # exemption list is for strings that are not site paths, and this one was
    # trying to be.

    def _ordinary(self, i):
        if i % 3 == 0:                      # id-bearing URLs are the distinct ones
            return f'/legislation/{i}/vote/'
        return self.ORDINARY_ROUTES[i % len(self.ORDINARY_ROUTES)]

    def test_a_full_buffer_of_ordinary_traffic_is_under_budget(self):
        """
        ⚠️ THE HALF THAT v3.19.4 FAILED AND EVERY EARLIER VERSION PASSED.

        `MAX_STORED` ordinary requests is the normal steady state of a busy site.
        If that trips the recommendation, the recommendation is measuring uptime.
        """
        from src.middleware import performance

        entries = self._entries(performance.MAX_STORED, self._ordinary)
        over, size = performance.buffer_is_over_budget(entries)

        self.assertFalse(
            over,
            f'A full buffer of ordinary traffic ({size} bytes, '
            f'{size / performance.MAX_STORED:.0f} B/entry) must not be flagged — '
            f'that is what the buffer is for.',
        )

    def test_a_full_buffer_of_very_long_paths_is_over_budget(self):
        """
        ⚠️ THE HALF v3.19.4 COULD NOT PASS AT ANY INPUT, which is what made it
        decoration.

        `path` is the only part of the tuple that varies (the rest is three
        numbers and a timestamp), so this is the realistic way the buffer grows —
        a scanner sweeping long URLs, or a route gaining a long serialised query
        parameter. Measured at ~2,059 B/entry, ~1 MB for a full buffer.
        """
        from src.middleware import performance

        entries = self._entries(
            performance.MAX_STORED, lambda i: f'/search/?q={i}' + 'a' * 2000)
        over, size = performance.buffer_is_over_budget(entries)

        self.assertTrue(
            over,
            f'A full buffer of 2 KB paths ({size} bytes) must be flagged, or the '
            f'check can never fire and is not a check.',
        )

    def test_a_cold_process_does_not_trip_the_budget(self):
        """
        ⚠️ v3.19.6 — THIS TEST USED TO ASSERT THE OPPOSITE OF WHAT IT SAID, AND
        IT PASSED BECAUSE OF THE BUG IT WAS WRITTEN TO PREVENT.

        It was `test_the_budget_is_relative_to_the_bound_it_is_about`, and it
        fed ten entries of 2 KB paths — ~2,063 B/entry, more than tenfold over
        `BYTES_PER_ENTRY_BUDGET` — then asserted `assertFalse(over)`, reasoning:

            'Ten entries cannot exceed a budget expressed per entry, whatever
             they contain'

        Against a budget that really is per entry, ten entries of 2 KB paths are
        precisely what DOES exceed it. The assertion held only because
        `buffer_is_over_budget` was comparing against `MAX_STORED *
        BYTES_PER_ENTRY_BUDGET` — a fixed 96,000-byte TOTAL — which is the defect
        this class exists to catch, described in three places as per-entry and
        locked in by this test.

        The worry underneath it was real and is kept: a cold process must not
        trip the check. That is now `MIN_ENTRIES_FOR_BUDGET`, an explicit floor,
        rather than an accidental factor of `MAX_STORED / len(entries)`.

        **The rule this adds to the class: a test whose justification and whose
        assertion can both be true of different code has not pinned either.**
        """
        from src.middleware import performance

        def long_path(i):
            return f'/x/{i}/' + 'a' * 2000

        # Below the floor: no meaningful average, so no answer.
        under_floor, _ = performance.buffer_is_over_budget(
            self._entries(performance.MIN_ENTRIES_FOR_BUDGET - 1, long_path))
        self.assertFalse(
            under_floor,
            'A cold process with a handful of entries has no meaningful average '
            'and must not trip the budget.',
        )

        # One entry above the floor, same per-entry cost: now it answers.
        over_floor, _ = performance.buffer_is_over_budget(
            self._entries(performance.MIN_ENTRIES_FOR_BUDGET, long_path))
        self.assertTrue(
            over_floor,
            'Once there are enough entries to average, 2 KB paths are over '
            'budget at ANY occupancy — that is what "per entry" means.',
        )

    def test_the_answer_does_not_depend_on_how_full_the_buffer_is(self):
        """
        ⚠️ v3.19.6 — THE ASSERTION THIS CLASS WAS MISSING, and the one that fails
        against every previous version of the check.

        `EveryThresholdHasBothAnswers` asked its question at full occupancy only.
        Measured 08-10-26 on entries of 200-character paths, all at 248 B/entry —
        29 % over the stated budget — the old total-based check answered:

            500 entries  123,980 B  fires
            400 entries   99,180 B  fires
            387 entries   95,956 B  SILENT
            200 entries   49,571 B  SILENT
            100 entries   24,771 B  SILENT

        Same buffer, same per-entry cost, four different answers. A partial
        buffer is not exotic — `CACHE_TTL` is 25 h and sampling is 1-in-20, so
        filling 500 slots takes ~10,000 non-slow requests, and `memory_report` is
        run *after* someone notices something, often after a restart.

        A budget expressed per entry must be scale-invariant above the floor.
        """
        from src.middleware import performance

        # 248 B/entry: over the 192 B budget, but nowhere near the old 96,000 B
        # total until the buffer is ~77 % full. That gap is the bug.
        def long_path(i):
            return f'/x{i}/' + 'a' * 195

        answers = {
            n: performance.buffer_is_over_budget(self._entries(n, long_path))[0]
            for n in (performance.MIN_ENTRIES_FOR_BUDGET, 100, 200, 387, 500)
        }

        self.assertEqual(
            set(answers.values()), {True},
            f'The same per-entry cost gave different answers at different '
            f'occupancies: {answers}. The budget is per entry; the answer must '
            f'not depend on how full the buffer happens to be.',
        )

    def test_the_recommendation_reports_the_comparison_it_actually_made(self):
        """
        ⚠️ v3.19.6 — THE NAMING HALF, which is what went wrong this time.

        The first three versions of this threshold were wrong about the WORLD
        (always true, always true, never true). The fourth was wrong about
        ITSELF: the code compared a total and the message said "N B/entry vs a
        192 B budget", so a reader checking the arithmetic would have concluded
        the check was fine.

        This asserts the printed quantity is the tested quantity, by requiring
        that a buffer whose per-entry average is under budget is not flagged
        however many entries it holds — which is only true if the comparison is
        the one the message describes.
        """
        from src.middleware import performance

        # ~54 B/entry: comfortably under the 192 B budget. Under the old total
        # comparison a large enough buffer of these would eventually cross
        # 96,000 bytes and be flagged while the message claimed 54 < 192.
        for n in (performance.MIN_ENTRIES_FOR_BUDGET, 500):
            over, size = performance.buffer_is_over_budget(
                self._entries(n, self._ordinary))
            self.assertFalse(
                over,
                f'{n} ordinary entries ({size} B, {size / n:.0f} B/entry) are '
                f'under the {performance.BYTES_PER_ENTRY_BUDGET} B budget and '
                f'must not be flagged — the message would say so either way.',
            )

    def test_an_empty_buffer_is_zero_bytes_and_not_an_error(self):
        from src.middleware import performance

        over, size = performance.buffer_is_over_budget([])
        self.assertEqual(size, 0)
        self.assertFalse(over)

    def test_the_headroom_the_budget_was_chosen_for_still_exists(self):
        """
        ⚠️ The assertion that keeps `BYTES_PER_ENTRY_BUDGET` honest.

        192 was chosen to sit ~3x above realistic traffic and below a buffer of
        200-character paths. If the entry shape changes enough that ordinary
        traffic creeps toward the budget, the check quietly turns into the
        always-true one it replaced — and the next person to touch it will be
        reasoning from this docstring rather than from the tree. Deliberately
        loose: it asserts the headroom, not the number.
        """
        from src.middleware import performance

        entries = self._entries(performance.MAX_STORED, self._ordinary)
        per_entry = performance.buffer_size_bytes(entries) / performance.MAX_STORED

        self.assertLess(
            per_entry, performance.BYTES_PER_ENTRY_BUDGET / 2,
            f'Ordinary entries now cost {per_entry:.0f} B, over half the '
            f'{performance.BYTES_PER_ENTRY_BUDGET} B budget. Re-measure and '
            f're-derive the budget rather than raising it — the headroom was '
            f'the point, and a threshold with none is the bug this replaced.',
        )

    def test_identical_entries_do_not_measure_the_buffer(self):
        """
        ⚠️ THE MEASUREMENT TRAP ITSELF, asserted so nobody re-derives a threshold
        from a memoised fixture the way all three previous versions did.

        Same count, same tuple shape, ~2x the bytes — the only difference is
        whether the entries repeat. If this ever stops holding, the serialiser
        changed and every byte figure in this module needs re-deriving.
        """
        from src.middleware import performance

        now = timezone.now()
        identical = [(now, 12.5, '/legislation/history/', 3, 4.2)
                     for _ in range(performance.MAX_STORED)]
        realistic = self._entries(performance.MAX_STORED, self._ordinary)

        self.assertGreater(
            performance.buffer_size_bytes(realistic),
            performance.buffer_size_bytes(identical) * 1.5,
            'pickle memoises repeated objects, so a fixture of identical entries '
            'reports a fraction of the real cost. Vary the path when measuring.',
        )

    def test_the_command_reports_the_same_number_it_judges(self):
        """
        Section 6 prints a buffer size and `_show_recommendations` decides whether
        to warn about one. Before v3.19.5 those were two separate `pickle.dumps`
        calls over two separate `cache.get`s, which is two chances to disagree —
        and disagreeing is precisely how someone reads "13 KB" and a warning that
        it is too large in the same output and believes the warning.
        """
        import inspect

        from src.management.commands import memory_report

        source = inspect.getsource(memory_report)
        self.assertNotIn(
            'pickle.dumps', source,
            'memory_report must measure through performance.buffer_size_bytes, '
            'not with its own pickle call.',
        )
        self.assertIn('buffer_size_bytes', source)
        self.assertIn('buffer_is_over_budget', source)
