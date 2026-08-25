"""
Every `inet` column can say "no address", and every reader copes (v3.21.7).

⚠️ THE PRECONDITION FOR THE WHOLE BUG CLASS, STATED ONCE.

v3.21.6 found the `'unknown'` sentinel reaching `ActivityLog.ip_address` and
fixed it. v3.21.7 found the same crossing at nine other writers and fixed it at
the source — `get_client_ip` now returns an address or `None`.

That closed the attacker-triggerable route and could not close the rest, because
three of the ten columns were **NOT NULL**:

    HoneypotAccess.ip_address
    LoginLockout.ip_address
    QuarantinedAccount.ip_address

`None` into a NOT NULL column is an `IntegrityError` — the same failure with a
different exception class. **Moving a failure is not fixing it.** So migration
`0020` makes them nullable, and this module pins the property that made all of
this possible in the first place:

> **A column that stores a client's address must be able to say it does not
> have one.** A schema that cannot express "unknown" forces every writer to
> invent a value, and an invented value in a typed column is wrong data.

⚠️ AND UNLIKE THE WALK IT REPLACES, THIS ONE CAN FAIL.

v3.21.6's `TheSentinelIsInvalidForEveryInetColumnTests` walks `apps.get_models()`
and asserts `field.clean('unknown', …)` raises — a property of
`django.db.models.GenericIPAddressField`, true in every Django project ever
written, and therefore incapable of going red no matter what this codebase does.

`test_every_inet_column_can_store_no_address` below walks the same models and
asserts something about **this schema**: it goes red the day somebody adds a
NOT NULL `GenericIPAddressField`, which is the structural precondition for the
next instance of this bug. That is the difference between enumerating a
population and enumerating the framework.

⚠️ NAMED GAP: this is a schema assertion, not a writer assertion. It cannot see
a writer that stores a *wrong* address, only one that has no way to store none.
The writer side is covered by `test_client_ip_is_always_an_address.py`, at the
single source every writer draws from.
"""
from django.apps import apps
from django.core.cache import cache
from django.db import models
from django.test import Client, TestCase
from django.urls import reverse

from src.models import (HoneypotAccess, IPBlacklist, IPWhitelist, LoginLockout,
                        ParliamentUser, QuarantinedAccount)
from src.utils.security_utils import MISSING_IP_SENTINEL


class TheSchemaCanExpressNoAddressTests(TestCase):

    def _inet_fields(self):
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if isinstance(field, models.GenericIPAddressField):
                    yield model, field

    def test_the_walk_finds_the_columns_we_know_about(self):
        """A walk that matches nothing passes everything below vacuously."""
        found = {f'{m._meta.label}.{f.name}' for m, f in self._inet_fields()}
        self.assertIn('src.HoneypotAccess.ip_address', found)
        self.assertGreaterEqual(len(found), 10, f'only found {sorted(found)}')

    def test_every_inet_column_can_store_no_address(self):
        for model, field in self._inet_fields():
            with self.subTest(column=f'{model._meta.label}.{field.name}'):
                self.assertTrue(
                    field.null,
                    f'{model._meta.label}.{field.name} is NOT NULL, so a writer '
                    f'with no address must either invent a value — which is how '
                    f'the sentinel got into an inet column — or lose the row.',
                )


class TheHoneypotSurvivesWithNoAddressTests(TestCase):
    """
    The honeypot is the sharpest of the three: it is reached by unauthenticated
    scanners by design, so it is the one place where "the write failed" turns
    into a distinguishing 500 that tells the scanner the path is real.
    """

    def setUp(self):
        cache.clear()

    def test_a_hit_with_no_resolvable_address_stores_null(self):
        response = Client().get(reverse('honeypot_wp_admin'), REMOTE_ADDR='')

        self.assertNotEqual(response.status_code, 500)
        stored = list(HoneypotAccess.objects.values_list('ip_address', flat=True))
        self.assertEqual(
            stored, [None],
            f'Expected NULL. {MISSING_IP_SENTINEL!r} here is what PostgreSQL '
            f'refuses outright and SQLite silently accepts.',
        )

    def test_it_is_still_banned_in_cache(self):
        """
        The row is the optional half. The ban is the point, and it must not be
        keyed on the string "None" either — that is a sentinel by accident.
        """
        Client().get(reverse('honeypot_wp_admin'), REMOTE_ADDR='')

        self.assertTrue(
            cache.get(f'honeypot_ban_{MISSING_IP_SENTINEL}'),
            'The address-less bucket must still be banned.',
        )
        self.assertIsNone(cache.get('honeypot_ban_None'))

    def test_no_junk_blacklist_row_is_created(self):
        """
        `IPBlacklist` matches by exact equality on the client's address. A row
        written for a client with no address could never match anything, so it
        would be a ban that reads as coverage and protects nobody — the exact
        defect the v3.21.7 audit found with forged headers.
        """
        Client().get(reverse('honeypot_wp_admin'), REMOTE_ADDR='')

        self.assertEqual(IPBlacklist.objects.count(), 0)


class TheLockoutTablesAcceptNoAddressTests(TestCase):

    def test_quarantine_normalises_the_sentinel(self):
        """
        Normalised in `quarantine_user`, which is the model's single entry
        point — both callers hand it `get_client_ip(...) or 'unknown'`.
        """
        user = ParliamentUser.objects.create(
            user_id='P-QUAR', name='Quarantined', username='P-QUAR',
            member_type='Pledge', member_status='Active',
        )

        record = QuarantinedAccount.quarantine_user(
            user=user, ip_address=MISSING_IP_SENTINEL, reason='test',
        )

        self.assertIsNone(record.ip_address)
        self.assertTrue(
            ParliamentUser.objects.get(pk=user.pk).is_quarantined,
            'CONTROL — the quarantine itself must still take effect.',
        )

    def test_quarantine_keeps_a_real_address(self):
        """CONTROL. Normalising must not blank the working case."""
        user = ParliamentUser.objects.create(
            user_id='P-QUAR2', name='Quarantined', username='P-QUAR2',
            member_type='Pledge', member_status='Active',
        )

        record = QuarantinedAccount.quarantine_user(
            user=user, ip_address='203.0.113.9', reason='test',
        )

        self.assertEqual(record.ip_address, '203.0.113.9')

    def test_a_lockout_row_can_be_written_with_no_address(self):
        """
        Before `0020` this raised, and every writer wrapped it in
        `except Exception: pass` — so the row was silently dropped and the
        admin console showed nothing at all. A NULL row is strictly more.
        """
        from django.utils import timezone
        from datetime import timedelta

        row = LoginLockout.objects.create(
            ip_address=None, username='someone', source='middleware_user',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        self.assertIsNone(LoginLockout.objects.get(pk=row.pk).ip_address)


class TheAdminActionsCopeWithNoAddressTests(TestCase):
    """
    ⚠️ THE HALF THAT IS EASY TO FORGET. Widening a column is one line; the cost
    is paid by every reader that assumed the old constraint. Three readers took
    the value straight into another NOT NULL column, and the worst of them is
    the one an admin reaches for when a member is locked out and needs to get
    back in.
    """

    def setUp(self):
        from unittest import mock

        from django.utils import timezone as tz

        from src.view import admin_v2

        self.admin = ParliamentUser.objects.create_user(
            user_id='900', password='null-ip-admin-pass-12345!',
            name='Admin Aardvark', username='admin_null_ip',
            member_type='Member', is_admin=True,
        )
        # ⚠️ `ALLOWED_USER_IDS` is patched rather than read from the ambient
        # environment. v3.21.5 records what happens otherwise: fifteen admin-v2
        # tests read `ADMIN_V2_USER_IDS` out of a gitignored `.env`, passed on
        # one laptop, and kept CI red for four hundred runs.
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {'900'})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client = Client()
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = tz.now().isoformat()
        session.save()

    def test_bulk_blacklisting_honeypot_ips_skips_the_address_less_rows(self):
        # ⚠️ Real honeypot routes, not '/a/' and '/b/'. The first draft used
        # those and `test_hardcoded_urls` failed the build on them — correctly:
        # they are path-shaped strings that resolve to nothing, which is exactly
        # what a renamed route leaves behind.
        HoneypotAccess.objects.create(
            endpoint=reverse('honeypot_wp_admin'), ip_address=None)
        HoneypotAccess.objects.create(
            endpoint=reverse('honeypot_phpmyadmin'), ip_address='203.0.113.9')

        response = self.client.post(reverse('admin_v2_blacklist_all_honeypot_ips'))

        self.assertNotEqual(response.status_code, 500)
        self.assertEqual(
            list(IPBlacklist.objects.values_list('ip_address', flat=True)),
            ['203.0.113.9'],
            'The NULL row must be skipped, not written into a NOT NULL column.',
        )

    def _lockout_with_no_ip(self):
        from django.utils import timezone
        from datetime import timedelta
        return LoginLockout.objects.create(
            ip_address=None, username='locked-out', source='middleware_user',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def test_blacklisting_a_lockout_with_no_ip_does_not_500(self):
        lockout = self._lockout_with_no_ip()

        response = self.client.post(
            reverse('admin_v2_lockouts'),
            {'action': 'blacklist', 'lockout_id': lockout.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(IPBlacklist.objects.count(), 0)

    def test_whitelisting_a_lockout_with_no_ip_does_not_500(self):
        """
        The sharper of the two: an `IntegrityError` here would have fired
        BEFORE `clear_lockouts_for`, so the member would have stayed locked out
        and the admin would have seen a 500 while trying to help him.
        """
        lockout = self._lockout_with_no_ip()

        response = self.client.post(
            reverse('admin_v2_lockouts'),
            {'action': 'whitelist_and_clear', 'lockout_id': lockout.pk},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(IPWhitelist.objects.count(), 0)


class TheBlacklistColumnRejectsNonAddressesTests(TestCase):
    """
    `IPBlacklist.ip_address` is a `CharField` and stays one this release — the
    conversion to `inet` is `USING ip_address::inet`, which hard-fails mid-deploy
    on any row Postgres cannot cast, and nobody knows what production holds.
    `manage.py preflight` counts them; these pin the two guards that ship now.
    """

    def test_the_field_validates_addresses(self):
        from django.core.exceptions import ValidationError

        field = IPBlacklist._meta.get_field('ip_address')
        for value in (MISSING_IP_SENTINEL, 'not-an-ip', "'; DROP--"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    field.clean(value, IPBlacklist())

    def test_a_cidr_range_is_rejected_because_nothing_honours_one(self):
        """
        The help text promised "IP address or CIDR range" for months. Every
        consumer matches with `filter(ip_address=<client address>)` — exact
        equality — so a range has never blocked a single request. An entry that
        reads as coverage and provides none is worse than no entry.
        """
        from django.core.exceptions import ValidationError

        field = IPBlacklist._meta.get_field('ip_address')
        with self.assertRaises(ValidationError):
            field.clean('10.0.0.0/8', IPBlacklist())

        self.assertNotIn('CIDR range to block', field.help_text)

    def test_a_real_address_still_passes(self):
        """CONTROL."""
        field = IPBlacklist._meta.get_field('ip_address')
        self.assertEqual(field.clean('203.0.113.9', IPBlacklist()), '203.0.113.9')
