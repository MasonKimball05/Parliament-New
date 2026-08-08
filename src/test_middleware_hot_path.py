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

⚠️ NOT YET EXECUTED — see the changelog. These were written in an environment
with no working Django (the repo `.venv` is a macOS interpreter). Run
`manage.py test src.test_middleware_hot_path` before deploying. Where a test
would pass for the wrong reason against the pre-fix tree, that is called out
on the test itself.
"""

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
        user = make_user('perf-user')
        cache.clear()
        client = Client()
        client.force_login(user)
        client.get(reverse('home'))

        from src.middleware.performance import _get_entries

        entries = [e for e in _get_entries() if e[2] == reverse('home')]
        self.assertTrue(entries, 'No metric was recorded for the home page.')
        self.assertGreater(
            entries[-1][3], 0,
            'The home page was recorded as costing 0 queries. That is the '
            'number this release exists to stop reporting.',
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
        admin = make_user('debug-admin', is_admin=True)
        cache.clear()
        client = Client()
        client.force_login(admin)
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

        src_root = pathlib.Path(__file__).resolve().parent
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
