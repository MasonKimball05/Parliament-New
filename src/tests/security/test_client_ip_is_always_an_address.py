"""
`get_client_ip` returns an address or `None`, and never anything else (v3.21.7).

⚠️ WHAT THIS IS ABOUT, AND WHY IT IS A SEPARATE MODULE FROM `test_ip_sentinel`.

v3.21.6 fixed a real bug — the `'unknown'` sentinel reaching `ActivityLog`'s
`inet` column — at the point of storage, which was the right place for that
model. It then wrote `TheSentinelIsInvalidForEveryInetColumnTests`, described in
its own docstring as *the enumeration* the v3.19.6 rule demands, quoting that
rule by name.

**It enumerates the wrong population.** Read what it asserts:

    field.clean(MISSING_IP_SENTINEL, model())   # must raise ValidationError

That is a property of `django.db.models.GenericIPAddressField`. It holds in
every Django project ever written, it held before v3.21.6, and it will hold
after any future regression here. A walk over `apps.get_models()` makes it look
like a survey of this codebase; what it actually surveys is Django.

The population that mattered was never the columns. It was the **writers**.
There are ten inet columns and v3.21.6 normalised the writer of one of them —
the one CI happened to fail on — while `HoneypotAccess`, `LoginLockout`,
`QuarantinedAccount`, `CSPViolation`, `SecurityNotificationLog`,
`APIAccessLog`, `AdminActionLog`, `SlatingActivity` and `UserSession` were
untouched, and `HoneypotAccess.ip_address` is `NOT NULL`.

> **A walk that cannot fail is not an enumeration. Before trusting one, ask what
> would have to be true for it to go red — and if the answer is "Django would
> have to change", it is measuring the framework, not the code.**

⚠️ WHY THE FIX IS HERE AND NOT AT NINE MORE CALL SITES.

`get_client_ip` is the single source: v3.18.8 consolidated five inline copies
onto it, and `test_client_ip_single_source.py` fails the build if a sixth
appears. So one validation at the source covers all ten columns *and* the four
cache keys built from the same value — and it covers writers nobody has written
yet, which is the difference between fixing a population and fixing a list.

⚠️ THE ATTACKER-CONTROLLED ROUTE, MEASURED.

`CF-Connecting-IP` is honoured whenever `BEHIND_CLOUDFLARE=True` and
`CLOUDFLARE_VERIFY_ORIGIN=False` — the shipped default, and v3.19.3 turned it on
precisely because the origin *is* directly reachable. Before this release, a
request carrying `CF-Connecting-IP: '; DROP--` made `get_client_ip` return that
string verbatim, and it was written into two model columns and four cache keys.

The residual is named rather than hidden: a forged header that *is* a
well-formed address still wins, and still lets a direct-to-origin client pick
its own rate-limit bucket. The fix for that is a firewall rule. Narrowing a hole
is not closing it, and this module does not claim otherwise.
"""
from django.test import Client, RequestFactory, TestCase, override_settings

from src.models import HoneypotAccess, IPBlacklist
from src.utils.security_utils import MISSING_IP_SENTINEL, get_client_ip

#: Values a client can put in a header. Not exotic: the first is the shape that
#: broke CI, the rest are what a scanner or a fuzzer sends without trying.
NOT_ADDRESSES = (
    MISSING_IP_SENTINEL,
    "'; DROP--",
    'not-an-ip',
    '999.999.999.999',
    '127.0.0.1, 10.0.0.1',      # a list, not an address
    '203.0.113.7:443',          # with a port
    '<script>alert(1)</script>',
    'x' * 300,
    '   ',
)


class TheAnswerIsAlwaysAnAddressOrNoneTests(TestCase):
    """The property. Every consumer of this function depends on it."""

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_a_junk_cf_header_never_becomes_the_answer(self):
        for value in NOT_ADDRESSES:
            with self.subTest(header=value[:40]):
                request = RequestFactory().get(
                    '/', HTTP_CF_CONNECTING_IP=value, REMOTE_ADDR='203.0.113.9',
                )
                self.assertEqual(
                    get_client_ip(request), '203.0.113.9',
                    'A CF header that is not an address must lose to the socket '
                    'peer, not beat it.',
                )

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_a_junk_cf_header_with_no_peer_either_yields_none(self):
        for value in NOT_ADDRESSES:
            with self.subTest(header=value[:40]):
                request = RequestFactory().get('/', HTTP_CF_CONNECTING_IP=value)
                request.META.pop('REMOTE_ADDR', None)
                self.assertIsNone(get_client_ip(request))

    def test_a_junk_forwarded_for_never_becomes_the_answer(self):
        # ⚠️ The comma case is deliberately excluded, and the first draft of
        # this test failed on it. A comma-separated list is junk in
        # `CF-Connecting-IP`, which carries exactly one address — and it is the
        # NORMAL shape of `X-Forwarded-For`, where the whole point is to split
        # it and take the rightmost entry. One corpus, two headers, and the
        # same string means opposite things in each.
        for value in [v for v in NOT_ADDRESSES if ',' not in v]:
            with self.subTest(header=value[:40]):
                request = RequestFactory().get('/', HTTP_X_FORWARDED_FOR=value)
                request.META.pop('REMOTE_ADDR', None)
                self.assertIsNone(get_client_ip(request))

    def test_a_missing_remote_addr_yields_none_not_empty_string(self):
        """
        The `force_login` condition — empty `META` — which is what produced all
        fifty errors in CI run #401.
        """
        request = RequestFactory().get('/')
        request.META.pop('REMOTE_ADDR', None)
        self.assertIsNone(get_client_ip(request))


class TheWorkingCasesStillWorkTests(TestCase):
    """
    CONTROLS. A function that returns `None` for everything satisfies every
    assertion above and is the v3.18.8 bug wearing a fix's clothes.
    """

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_a_real_cf_header_still_wins(self):
        request = RequestFactory().get(
            '/', HTTP_CF_CONNECTING_IP='198.51.100.4', REMOTE_ADDR='172.70.231.106',
        )
        self.assertEqual(get_client_ip(request), '198.51.100.4')

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_a_real_ipv6_cf_header_still_wins(self):
        request = RequestFactory().get(
            '/', HTTP_CF_CONNECTING_IP='2001:db8::1', REMOTE_ADDR='172.70.231.106',
        )
        self.assertEqual(get_client_ip(request), '2001:db8::1')

    def test_the_rightmost_forwarded_for_entry_still_wins(self):
        """
        Pins v3.18.8's actual finding: rightmost, because that is nginx's own
        append. Leading entries are client-supplied.
        """
        request = RequestFactory().get(
            '/', HTTP_X_FORWARDED_FOR='10.0.0.1, 192.0.2.5, 203.0.113.9',
        )
        self.assertEqual(get_client_ip(request), '203.0.113.9')

    def test_a_plain_remote_addr_still_wins(self):
        request = RequestFactory().get('/', REMOTE_ADDR='203.0.113.9')
        self.assertEqual(get_client_ip(request), '203.0.113.9')


class TheHoneypotStoresAnAddressTests(TestCase):
    """
    The end-to-end reproduction, through a real request rather than a helper.

    v3.21.2's rule: *a scanner approximates; a request does not.* The unit tests
    above pin the function; these pin what reaches the column, which is the
    thing that was actually wrong.
    """

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_a_forged_junk_header_does_not_reach_the_inet_column(self):
        Client().get(
            '/wp-admin/', HTTP_CF_CONNECTING_IP='not-an-ip', REMOTE_ADDR='203.0.113.9',
        )

        stored = list(HoneypotAccess.objects.values_list('ip_address', flat=True))
        self.assertEqual(
            stored, ['203.0.113.9'],
            'Before v3.21.7 this stored the header verbatim. On PostgreSQL that '
            'row cannot be inserted at all — the column is inet and NOT NULL — '
            'and the INSERT was unwrapped, so the failure took the ban with it.',
        )

    @override_settings(BEHIND_CLOUDFLARE=True, CLOUDFLARE_VERIFY_ORIGIN=False)
    def test_the_blacklist_entry_is_an_address_too(self):
        """
        `IPBlacklist.ip_address` is a CharField, so it accepted the junk on
        every backend and never raised. That is worse, not better: the ban was
        recorded under a key no real client can ever match, so the entry existed
        and protected nothing.
        """
        Client().get(
            '/wp-admin/', HTTP_CF_CONNECTING_IP='not-an-ip', REMOTE_ADDR='203.0.113.9',
        )

        self.assertEqual(
            list(IPBlacklist.objects.values_list('ip_address', flat=True)),
            ['203.0.113.9'],
        )

    def test_a_honeypot_hit_that_cannot_be_recorded_is_still_banned(self):
        """
        The second layer, and the one that does not depend on having enumerated
        the callers.

        The `HoneypotAccess` INSERT was the only unwrapped DB call on a path
        unauthenticated scanners reach by design, and it sits ABOVE the ban. So
        anything that made it fail returned a 500 — a *distinguishing* response,
        telling the scanner the path is real — while skipping the ban entirely.
        Recording the hit is the optional half.
        """
        from unittest import mock

        with mock.patch(
            'src.view.honeypot.HoneypotAccess.objects.create',
            side_effect=Exception('column "ip_address" is of type inet'),
        ):
            response = Client().get('/wp-admin/', REMOTE_ADDR='203.0.113.9')

        self.assertNotEqual(
            response.status_code, 500,
            'A honeypot that 500s has identified itself as a honeypot.',
        )
        self.assertTrue(
            IPBlacklist.objects.filter(ip_address='203.0.113.9').exists(),
            'The ban is the point. It must not be downstream of the log row.',
        )
