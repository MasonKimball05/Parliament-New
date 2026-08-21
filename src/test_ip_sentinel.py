"""
The missing-IP sentinel must never reach an `inet` column (v3.21.6).

⚠️ WHAT HAPPENED. CI run #401 reported **50 errors**, and all fifty were one
bug:

    psycopg2.errors.InvalidTextRepresentation:
        invalid input syntax for type inet: "unknown"

`signals.log_successful_login` computes `get_client_ip(request) or 'unknown'`.
That is correct for `LoginHistory.ip_address`, an `EncryptedCharField` with
`null=False` which needs *some* string — the convention is deliberate and
`analyze_login_risk` tests for the sentinel by name. The same variable is then
passed to `ActivityLog.log_activity`, whose `ip_address` is a
`GenericIPAddressField` — `inet` on PostgreSQL, which accepts an address or NULL
and nothing else.

Every test that logged a pledge in hit it, because `Client.force_login` builds a
request with empty `META`, so `get_client_ip` returns nothing and the sentinel
takes over.

⚠️ AND THE REASON IT SURVIVED SINCE 05-28-26 IS THE PART WORTH KEEPING.
**SQLite has no `inet` type.** It stored `'unknown'` in that column without a
murmur, so the local suite was green, the pre-push hook was green, and eight
release notes said so honestly. The type system that would have caught this
exists only in production and in CI — and CI had never passed, so nobody read
it.

> **A backend difference is not a portability detail; it is a type check you
> only run in one place.** Where the local database is more permissive than the
> real one, "the tests pass" means "the tests pass on the permissive one".

Which is why the assertions below are written to fail on **SQLite too**: they
check the value that was stored, not whether the insert raised. A test that only
asserts "no exception" would still be green on SQLite with `'unknown'` sitting
in the column.
"""
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.test import Client, TestCase

from src.models import ActivityLog, LoginHistory, ParliamentUser
from src.utils.security_utils import MISSING_IP_SENTINEL, ip_or_none


def _make_pledge(uid='P-IPTEST'):
    user = ParliamentUser.objects.create(
        user_id=uid, name='Test Pledge', username=uid,
        member_type='Pledge', member_status='Active',
    )
    user.set_password('ip-sentinel-test-pass-12345!')
    user.save()
    return user


class TheSentinelNeverReachesAnInetColumnTests(TestCase):
    """The reproduction, and the thing CI was actually failing on."""

    def test_a_pledge_login_with_no_resolvable_ip_stores_null_not_the_sentinel(self):
        """
        `force_login` builds a request with empty META, which is exactly the
        condition that produced the sentinel in CI.
        """
        pledge = _make_pledge()

        Client().force_login(pledge)

        entry = ActivityLog.objects.filter(action_type='pledge_login').first()
        self.assertIsNotNone(
            entry,
            'No pledge_login row was written at all. The signal wraps this in '
            'try/except, so a failure here is silent — which is how the '
            'production symptom (missing officer-review rows) would look.',
        )
        self.assertIsNone(
            entry.ip_address,
            f'Stored {entry.ip_address!r}. On PostgreSQL this row cannot be '
            f'inserted at all; on SQLite it is stored and the column now holds '
            f'a value that is not an address.',
        )

    def test_a_real_ip_still_gets_through(self):
        """
        CONTROL. Normalising must not turn the working case into NULL — an
        activity log with no addresses is not a fix, it is the v3.18.8 bug in a
        new hat.
        """
        pledge = _make_pledge('P-IPREAL')

        entry = ActivityLog.log_activity(
            action_type='pledge_login', user=pledge,
            description='x', ip_address='203.0.113.7',
        )

        self.assertEqual(entry.ip_address, '203.0.113.7')

    def test_ipv6_is_not_mistaken_for_junk(self):
        """CONTROL. The validator is Django's own, so v6 must survive."""
        pledge = _make_pledge('P-IPV6')

        entry = ActivityLog.log_activity(
            action_type='pledge_login', user=pledge,
            description='x', ip_address='2001:db8::1',
        )

        self.assertEqual(entry.ip_address, '2001:db8::1')

    def test_login_history_still_receives_the_sentinel(self):
        """
        CONTROL, and the reason the fix is not "stop using the sentinel".

        `LoginHistory.ip_address` is NOT NULL, and v3.15.2 records that a `None`
        here used to crash the geo lookup and silently drop login tracking
        altogether. The sentinel is right for that model. The bug was never the
        sentinel; it was the sentinel crossing into a column with a type.
        """
        pledge = _make_pledge('P-IPHIST')

        Client().force_login(pledge)

        record = LoginHistory.objects.filter(user=pledge).first()
        self.assertIsNotNone(record)
        self.assertEqual(record.ip_address, MISSING_IP_SENTINEL)


class TheSentinelIsInvalidForEveryInetColumnTests(TestCase):
    """
    The enumeration.

    ⚠️ Written as a walk over `apps.get_models()` rather than a list, because
    CLAUDE.md records twice — v3.19.6 and v3.19.11 — that *a set is only the
    general form if something enumerates the population it is drawn from*, and
    both times the missing enumeration was a four-minute query nobody ran. This
    is that query. There are ten such columns and the one that broke was found
    by CI, not by the nine others being checked.
    """

    def _inet_fields(self):
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, models.GenericIPAddressField):
                    yield model, field

    def test_the_walk_finds_the_columns_we_know_about(self):
        """
        A walk that matches nothing passes every other assertion here
        vacuously — the same guard `test_singleton_rows` needed.
        """
        found = {f'{m._meta.label}.{f.name}' for m, f in self._inet_fields()}
        self.assertIn('src.ActivityLog.ip_address', found)
        self.assertGreaterEqual(len(found), 10, f'only found {sorted(found)}')

    def test_every_inet_column_rejects_the_sentinel(self):
        """
        Field-level validation runs identically on every backend, so this
        assertion holds on SQLite even though the *storage* error does not.
        That is the whole trick: it moves a Postgres-only failure into a check
        the laptop can make.
        """
        for model, field in self._inet_fields():
            with self.subTest(column=f'{model._meta.label}.{field.name}'):
                with self.assertRaises(
                    ValidationError,
                    msg=f'{model._meta.label}.{field.name} accepted '
                        f'{MISSING_IP_SENTINEL!r}, which PostgreSQL will not.',
                ):
                    field.clean(MISSING_IP_SENTINEL, model())

    def test_login_history_is_deliberately_not_one_of_them(self):
        """
        Pins the asymmetry rather than leaving it to be rediscovered: the model
        the sentinel exists for is precisely the one that is not an inet column.
        """
        field = LoginHistory._meta.get_field('ip_address')
        self.assertNotIsInstance(field, models.GenericIPAddressField)
        self.assertFalse(field.null)


class TheNormaliserTests(TestCase):
    def test_it_rejects_things_that_are_not_addresses(self):
        for value in (MISSING_IP_SENTINEL, '', None, '   ', 'not-an-ip',
                      '999.999.999.999', '127.0.0.1, 10.0.0.1'):
            with self.subTest(value=value):
                self.assertIsNone(ip_or_none(value))

    def test_it_keeps_things_that_are(self):
        for value in ('127.0.0.1', '203.0.113.7', '::1', '2001:db8::1'):
            with self.subTest(value=value):
                self.assertEqual(ip_or_none(value), value)

    def test_it_strips_surrounding_whitespace(self):
        self.assertEqual(ip_or_none('  203.0.113.7  '), '203.0.113.7')
