"""
v3.19.4 — `FORGED_CF_HEADER` logging, and the property that it cannot drown the
log it is written to.

WHAT THIS IS ABOUT
------------------
v3.19.3 added `CLOUDFLARE_VERIFY_ORIGIN`: when it is on, `CF-Connecting-IP` is
honoured only if the request's socket peer is a published Cloudflare address,
and a header arriving from anywhere else is ignored and logged `FORGED_CF_HEADER`
at WARNING. That is the right behaviour — the whole point of the setting is to
surface an origin that is reachable directly, and a silent detection is useless.

The logging was unthrottled, and three facts compounded:

  1. `get_client_ip` runs **more than once per request**.
     `InputSanitizationMiddleware` calls it unconditionally on every request;
     `SessionTrackingMiddleware` and `EmergencyLockdownMiddleware` call it on
     their own conditions. One forged request wrote several identical lines.
  2. The `security` logger's only file handler is a `RotatingFileHandler` at
     10 MB × 3 backups **shared with `django`, `src`, `admin_actions` and
     `function_calls`**. There is no separate security log to protect.
  3. The condition that fires it is, by construction, someone who has already
     found a route to the origin — i.e. someone able to repeat it at will.

**An alarm that destroys the record when it fires is the wrong shape**, and that
is the reason to fix it rather than any estimate of how hard it is to abuse.

THE TEST THAT MATTERS MOST is `test_rotating_the_forged_value_buys_no_extra_lines`.
The throttle is keyed on the SOCKET PEER, not on the forged header value,
because the forged value is chosen by the attacker: a throttle keyed on
attacker-controlled input is not a throttle, it is a per-attacker-choice
allowance. The peer is the one field in the request an outside client cannot
pick, which is the same reason `_peer_is_cloudflare` checks it.
"""
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings

from src.utils import security_utils
from src.utils.security_utils import _socket_peer, get_client_ip

#: A published Cloudflare v4 range member, for the "legitimate edge" cases.
CF_PEER = '104.16.0.1'
#: TEST-NET-2 / TEST-NET-3 (RFC 5737) — never a real visitor, never Cloudflare.
DIRECT_PEER = '198.51.100.7'
OTHER_PEER = '203.0.113.9'


@override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=True)
class TheForgedHeaderWarningIsThrottled(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        cache.clear()

    # ⚠️ v3.19.6 — THE LOGGER NOW EMITS TWO KINDS OF LINE AND THE TESTS MUST NOT
    # CONFLATE THEM.
    #
    # Until v3.19.6 a burst produced exactly one line per peer per window, so
    # `warn.call_count` was the whole story and four tests below asserted on it
    # directly. The suppressed-hit tally, though, was only ever flushed by the
    # NEXT line — so a burst that genuinely ENDED took its count to the grave,
    # which is the case the alarm exists for. It is now also flushed on the write
    # side at `FORGED_CF_COUNT_MILESTONES` (10/100/1,000/10,000).
    #
    # That is a deliberate, bounded increase in log volume: at most four extra
    # lines per peer per burst, against a throttle whose whole purpose is to stop
    # a rotating log filling up. The tests split the two so the throttle's real
    # property — *one THROTTLED line per peer per window, however many calls* —
    # is still asserted exactly, rather than loosened to "a few".
    _MILESTONE_MARKER = 'hits suppressed so far'

    def _split_lines(self, warn):
        """(throttled_lines, milestone_lines) from a patched logger.warning."""
        throttled, milestones = [], []
        for call in warn.call_args_list:
            fmt = call.args[0] if call.args else ''
            (milestones if self._MILESTONE_MARKER in fmt else throttled).append(call)
        return throttled, milestones

    def _request(self, peer, cf_ip='10.0.0.1', path='/login/'):
        """
        A request whose SOCKET PEER is `peer` and which carries a forged
        `CF-Connecting-IP`.

        The peer is the rightmost X-Forwarded-For entry, because that is what
        nginx's `$proxy_add_x_forwarded_for` appends (`nginx.conf:62`) and
        therefore what `_socket_peer` reads. The leading entry is attacker
        junk, present so the tests exercise the same parse the real path does.
        """
        return self.factory.get(
            path,
            HTTP_X_FORWARDED_FOR=f'9.9.9.9, {peer}',
            HTTP_CF_CONNECTING_IP=cf_ip,
        )

    # ───────────────────────────────────────────────────── the throttle itself

    def test_a_burst_from_one_peer_produces_one_line(self):
        """
        1,000 forged calls — 500 requests at two `get_client_ip` calls each,
        which is what the real middleware chain does — must produce one line.

        **Fails against the v3.19.3 tree**, which logged all 1,000.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            for _ in range(500):
                get_client_ip(self._request(DIRECT_PEER))
                get_client_ip(self._request(DIRECT_PEER))

        throttled, milestones = self._split_lines(warn)
        self.assertEqual(
            len(throttled), 1,
            'The detection must log once per peer per window, not once per call.',
        )
        # v3.19.6: 1,000 calls, of which the FIRST is the throttled line — so
        # 999 suppressions, crossing 10 and 100 and stopping one short of 1,000.
        # Asserted exactly rather than as "a few": a milestone set that fired on
        # every hit would also produce "more than one".
        self.assertEqual(len(milestones), 2)

    def test_rotating_the_forged_value_buys_no_extra_lines(self):
        """
        ⚠️ THE ONE THAT DECIDES WHETHER THE THROTTLE IS REAL.

        The attacker controls `CF-Connecting-IP` completely — that is the entire
        premise of the finding. If the throttle key included it, 200 made-up
        addresses would buy 200 log lines and the throttle would be decorative.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            for i in range(200):
                get_client_ip(self._request(DIRECT_PEER, cf_ip=f'203.0.113.{i % 256}'))

        throttled, milestones = self._split_lines(warn)
        self.assertEqual(
            len(throttled), 1,
            'The throttle must key on the socket peer, which the client cannot '
            'choose — never on the forged value, which is chosen for it.',
        )
        # 199 suppressed → crosses 10 and 100. Critically this is a function of
        # the COUNT and not of the 200 distinct forged values: rotating the
        # header must not buy milestone lines either.
        self.assertEqual(len(milestones), 2)

    def test_distinct_peers_are_throttled_independently(self):
        """
        The throttle must not become a global mute: a second source is new
        information and the point of the alarm is to report it.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            for peer in (DIRECT_PEER, OTHER_PEER, '198.51.100.20'):
                for _ in range(10):
                    get_client_ip(self._request(peer))

        self.assertEqual(warn.call_count, 3)

    def test_the_suppressed_count_rides_on_the_next_line(self):
        """
        Throttling must not hide the volume. The suppressed hits are counted and
        the count is attached to the next line that gets through, so the log
        still answers "how much of this is there".
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            for _ in range(50):
                get_client_ip(self._request(DIRECT_PEER))

            cache.delete(f'forged_cf_seen_{DIRECT_PEER}')      # window elapses
            get_client_ip(self._request(DIRECT_PEER))

        throttled, _ = self._split_lines(warn)
        self.assertEqual(len(throttled), 2)
        self.assertIn('49 further hits suppressed', throttled[1].args[-1])

    def test_the_first_line_carries_no_suppression_suffix(self):
        """A clean first sighting should read as one, not as "0 suppressed"."""
        with patch.object(security_utils.logger, 'warning') as warn:
            get_client_ip(self._request(DIRECT_PEER))

        self.assertEqual(warn.call_args.args[-1], '')

    def test_the_count_survives_a_burst_that_stops(self):
        """
        ⚠️ v3.19.5 — THE CASE THE COUNT EXISTS FOR, AND THE ONE IT USED TO LOSE.

        v3.19.4 gave the gate key and the counter key the same TTL. They are
        written at different moments — the gate by the FIRST hit, the counter by
        the SECOND — so the counter expired a moment AFTER the gate, and was only
        ever read by a hit landing inside that sliver. A **burst that stops** (a
        scanner sweep, a one-off probe run: exactly the shape this alarm is meant
        to characterise) had its whole tally expire unread, and the operator got
        one line with no number, unable to tell one probe from ten thousand.

        Simulated the way the cache actually behaves: the gate expires, the
        counter does not, and a hit arriving well after the window still carries
        the tally.

        **Fails against the v3.19.4 tree** — there both keys were given
        `FORGED_CF_LOG_WINDOW`, so deleting the counter alongside the gate (which
        is what expiry does) leaves nothing to report.
        """
        from src.utils.security_utils import (
            FORGED_CF_COUNT_TTL, FORGED_CF_LOG_WINDOW,
        )

        self.assertGreater(
            FORGED_CF_COUNT_TTL, FORGED_CF_LOG_WINDOW,
            'The counter must outlive the gate, or a burst that ends is never '
            'reported. Equal TTLs read as equal lifetimes and are not, because '
            'the two keys are created one hit apart.',
        )

        with patch.object(security_utils.logger, 'warning') as warn:
            for _ in range(30):
                get_client_ip(self._request(DIRECT_PEER))

            # The burst ends. Only the GATE expires — the counter's longer TTL is
            # the entire fix, so only the gate is removed here.
            cache.delete(f'forged_cf_seen_{DIRECT_PEER}')

            # ...and much later, one more probe.
            get_client_ip(self._request(DIRECT_PEER))

        throttled, _ = self._split_lines(warn)
        self.assertEqual(len(throttled), 2)
        self.assertIn('29 further hits suppressed', throttled[1].args[-1])

    def test_a_burst_that_stops_AND_NEVER_RESUMES_still_reports_its_volume(self):
        """
        ⚠️ v3.19.6 — THE CASE THE TEST ABOVE DOES NOT COVER, AND THE REASON THE
        v3.19.5 FIX WAS INCOMPLETE.

        `test_the_count_survives_a_burst_that_stops` is named for a burst that
        stops and then **sends one more probe** — look at its last line. That
        probe is a rescue hit, and the longer `FORGED_CF_COUNT_TTL` only ever
        widens the window in which one can arrive. A sweep that genuinely ends
        never sends it, so under v3.19.5 the tally expired unread and the
        operator saw a single line with no number: exactly the symptom the
        v3.19.5 changelog claims to have fixed.

        **A tally flushed only by the next event cannot report the last event.**
        The flush has to be on the write side, which is
        `FORGED_CF_COUNT_MILESTONES`.

        No rescue hit here. The burst starts, runs, and stops.

        **Fails against the v3.19.5 tree**, where the only line emitted is the
        first one and it carries no volume at all.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            for _ in range(150):
                get_client_ip(self._request(DIRECT_PEER))
            # Nothing further. No expiry simulated, no later probe — the sweep
            # is simply over, which is what a sweep being over looks like.

        throttled, milestones = self._split_lines(warn)

        self.assertEqual(
            len(throttled), 1,
            'The throttle itself is unchanged: still one line per peer per window.',
        )
        self.assertTrue(
            milestones,
            'A burst that ends must still report its volume somewhere. With no '
            'later hit, the throttled line is the only one that will ever be '
            'written and it carries no count.',
        )
        self.assertEqual(
            [c.args[1] for c in milestones], [10, 100],
            '149 suppressed hits cross 10 and 100 and nothing else — the point '
            'is an order of magnitude, not a running commentary.',
        )
        self.assertIn(DIRECT_PEER, [c.args[2] for c in milestones])

    def test_milestones_are_per_peer_and_bounded(self):
        """
        The milestone lines must not reintroduce the flood the throttle removed.
        Four per peer per burst is the ceiling, by construction.
        """
        from src.utils.security_utils import FORGED_CF_COUNT_MILESTONES

        self.assertEqual(len(FORGED_CF_COUNT_MILESTONES), 4)

        with patch.object(security_utils.logger, 'warning') as warn:
            for peer in (DIRECT_PEER, OTHER_PEER):
                for _ in range(120):
                    get_client_ip(self._request(peer))

        throttled, milestones = self._split_lines(warn)
        self.assertEqual(len(throttled), 2, 'One per peer.')
        self.assertEqual(len(milestones), 4, 'Two per peer (10, 100), not shared.')

    def test_the_suppression_suffix_does_not_claim_a_window_it_cannot_know(self):
        """
        The suffix used to read "in the last 300s". Once the counter outlives the
        gate that is no longer true — a tally can span several windows — and a
        log line that states a duration it cannot vouch for is the same class of
        false comment this codebase has now deleted five of.

        ⚠️ v3.19.6 — THIS TEST FAILED AGAINST THE COMMIT THAT INTRODUCED IT.
        `git log -S` puts the assertion and the message it checks both in
        `b7c80be` (v3.19.5): the suffix was changed to *"since the last line"*
        and the assertion added was `assertNotIn('last ', suffix)`, which that
        very string contains. It has never passed. It was never run — the
        seventh consecutive batch of unexecuted tests.

        The INTENT was right and is kept: the suffix must not state a duration
        it cannot vouch for, because a tally can now span several windows.
        "since the last line" states no duration; it names an event, which is
        exactly the honest phrasing. So the assertion now looks for a claimed
        duration rather than for the word "last".
        """
        import re

        with patch.object(security_utils.logger, 'warning') as warn:
            for _ in range(5):
                get_client_ip(self._request(DIRECT_PEER))
            cache.delete(f'forged_cf_seen_{DIRECT_PEER}')
            get_client_ip(self._request(DIRECT_PEER))

        throttled, _ = self._split_lines(warn)
        suffix = throttled[1].args[-1]
        self.assertIn('suppressed', suffix)
        self.assertNotIn('300', suffix)
        self.assertIsNone(
            re.search(r'\d+\s*(s\b|sec|second|minute|hour)', suffix),
            f'The suffix must not claim a time window it cannot vouch for — a '
            f'tally can span several. Got: {suffix!r}',
        )

    # ─────────────────────────────────────────── the key cannot be steered

    def test_an_unparseable_peer_cannot_shape_the_cache_key(self):
        """
        With verification ON and no nginx in front, X-Forwarded-For is entirely
        client-supplied — so `_socket_peer` can return anything at all, and it
        is about to be interpolated into a cache key.

        Everything that is not an IP is bucketed under one key. Without this,
        a 5,000-character peer is a 5,000-character cache key, and a fresh one
        per request is both an unbounded write and a per-request line again.
        """
        junk = ['not-an-ip', 'a' * 5000, 'x y z', '127.0.0.1 evil', '::gg']

        with patch.object(security_utils.logger, 'warning') as warn:
            for peer in junk:
                get_client_ip(self._request(peer))

        self.assertEqual(
            warn.call_count, 1,
            'Every unparseable peer shares one bucket, or the throttle is '
            'bypassable by sending garbage.',
        )
        self.assertEqual(warn.call_args.args[2], 'unparseable')

    def test_the_forged_value_is_truncated_in_the_line(self):
        """A log field an attacker fills is a log field an attacker sizes."""
        with patch.object(security_utils.logger, 'warning') as warn:
            get_client_ip(self._request(DIRECT_PEER, cf_ip='9' * 4000))

        self.assertLessEqual(len(warn.call_args.args[1]), 64)

    def test_the_path_is_truncated_in_the_line(self):
        """
        Same reasoning for the path, which is also attacker-chosen. Django caps
        a URL well below this, so the bound is belt-and-braces rather than the
        only thing standing between the log and a long line.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            get_client_ip(self._request(DIRECT_PEER, path='/' + 'a' * 3000))

        self.assertLessEqual(len(warn.call_args.args[4]), 200)

    # ──────────────────────────────────────────────── the behaviour around it

    def test_a_real_cloudflare_peer_is_honoured_and_never_logged(self):
        """
        The control. If this fails the throttle tests above prove nothing,
        because a warning that never fires is trivially rate-limited.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            ip = get_client_ip(self._request(CF_PEER, cf_ip='72.14.201.5'))

        self.assertEqual(ip, '72.14.201.5', 'A genuine edge must still be trusted.')
        self.assertEqual(warn.call_count, 0)

    def test_a_forged_header_is_ignored_in_favour_of_the_peer(self):
        """
        Throttling the log must not have changed what the function returns. The
        forged value is discarded and the unforgeable peer is used.
        """
        with patch.object(security_utils.logger, 'warning'):
            ip = get_client_ip(self._request(DIRECT_PEER, cf_ip='1.2.3.4'))

        self.assertEqual(ip, DIRECT_PEER)

    @override_settings(CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_nothing_changes_when_verification_is_off(self):
        """
        The setting ships OFF. With it off the header is honoured exactly as
        before and nothing is logged — v3.19.3's deliberate no-op-on-deploy
        property, which v3.19.4 must not have disturbed.
        """
        with patch.object(security_utils.logger, 'warning') as warn:
            ip = get_client_ip(self._request(DIRECT_PEER, cf_ip='1.2.3.4'))

        self.assertEqual(ip, '1.2.3.4')
        self.assertEqual(warn.call_count, 0)


class SocketPeerReadsTheUnforgeableEnd(TestCase):
    """
    `_socket_peer` was split out of `_peer_is_cloudflare` in v3.19.4 so the log
    could throttle on the peer without computing it twice. These pin the parse,
    because the rightmost-vs-leftmost question is the one this codebase has
    already got wrong once — v3.18.8 existed because five call sites took the
    rightmost entry in a deployment where that was the Cloudflare edge.
    """

    def setUp(self):
        self.factory = RequestFactory()

    def test_the_rightmost_xff_entry_wins(self):
        request = self.factory.get('/', HTTP_X_FORWARDED_FOR='1.1.1.1, 2.2.2.2, 3.3.3.3')
        self.assertEqual(_socket_peer(request), '3.3.3.3')

    def test_remote_addr_is_used_when_there_is_no_xff(self):
        request = self.factory.get('/', REMOTE_ADDR='4.4.4.4')
        self.assertEqual(_socket_peer(request), '4.4.4.4')

    def test_whitespace_is_stripped(self):
        request = self.factory.get('/', HTTP_X_FORWARDED_FOR='1.1.1.1,   2.2.2.2   ')
        self.assertEqual(_socket_peer(request), '2.2.2.2')

    def test_an_absent_peer_is_empty_not_an_error(self):
        request = self.factory.get('/')
        request.META.pop('REMOTE_ADDR', None)
        self.assertEqual(_socket_peer(request), '')
