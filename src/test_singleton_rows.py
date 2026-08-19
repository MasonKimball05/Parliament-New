"""
v3.19.11 — every singleton accessor is a READ, and the row it hands back when
there is no row must be safe to save.

⚠️ WHY THIS FILE IS AN ENUMERATION AND NOT TWO TESTS.

v3.19.10 fixed `SystemLockdown.get_instance()` — a `get_or_create` under
middleware that runs on every request — carefully and with five tests, two of
which exist to catch the obvious wrong fix. Then it stopped, because
`SystemLockdown` was the model it was looking at.

`LandingPageContent.get_instance()` was the same three lines, on the public
unauthenticated landing page. Nothing pointed at it, so nothing found it.

CLAUDE.md's rule from v3.19.6, in as many words:

> **Building the general mechanism is not applying it to the general case.** A
> set is only the general form if something enumerates the population it is
> drawn from; otherwise it is an `if` with better manners.

So the tests below walk `apps.get_models()` and derive the population instead of
listing it. A third singleton added later is covered without anyone remembering
this file exists — which is the property the previous four "the helper exists,
three of six call sites use it" findings all lacked.

⚠️ **AND `test_the_walk_finds_the_two_we_know_about` IS NOT A FORMALITY.** A
walk that matches nothing passes every other assertion in this module
vacuously — an assertion that cannot distinguish the fix from an empty fixture
is not an assertion (CLAUDE.md, 08-03-26).
"""

from unittest.mock import patch

from django.apps import apps
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import LandingPageContent, ParliamentUser, SystemLockdown
from src.models.singleton import SingletonRow
from src.view import admin_v2 as admin_v2_module

#: Statement prefixes that mean the database was modified. `SAVEPOINT` and
#: `RELEASE` are included because on PostgreSQL a `get_or_create` shows up as
#: savepoint-insert-release and on sqlite as a bare INSERT — asserting only on
#: INSERT would make this suite quietly weaker on the backend production
#: actually runs.
_WRITE_PREFIXES = ('INSERT', 'UPDATE', 'DELETE', 'SAVEPOINT', 'RELEASE')


def singleton_models():
    """Every installed model exposing a `get_instance()` classmethod."""
    return [
        model for model in apps.get_models()
        if callable(getattr(model, 'get_instance', None))
    ]


def writes_in(captured):
    return [
        query['sql'] for query in captured
        if query['sql'].lstrip().upper().startswith(_WRITE_PREFIXES)
    ]


class EverySingletonAccessorIsAReadTests(TestCase):
    """The property, asserted over the derived population."""

    def test_the_walk_finds_the_two_we_know_about(self):
        found = {model.__name__ for model in singleton_models()}
        self.assertIn('SystemLockdown', found)
        self.assertIn('LandingPageContent', found)

    def test_no_get_instance_writes_when_the_row_is_absent(self):
        """
        ⚠️ FAILS AGAINST THE PRE-FIX TREE on `LandingPageContent`, whose
        `get_instance` was `get_or_create(pk=1)`. Verified 08-19-26 by running
        this against the accessor as it stood in `a168726`.

        The absent row is the case that matters: with the row present a
        `get_or_create` is indistinguishable from a read, which is exactly why
        this went unnoticed on a production database where the row has existed
        for a year.
        """
        for model in singleton_models():
            with self.subTest(model=model.__name__):
                model.objects.all().delete()
                cache.clear()

                with CaptureQueriesContext(connection) as captured:
                    model.get_instance()

                offenders = writes_in(captured.captured_queries)
                self.assertEqual(
                    offenders, [],
                    f'{model.__name__}.get_instance() wrote to the database '
                    f'while answering a question:\n  '
                    + '\n  '.join(sql[:120] for sql in offenders)
                    + '\n\nMix in SingletonRow (src/models/singleton.py) rather '
                      'than adding an exemption here.',
                )
                self.assertEqual(
                    model.objects.count(), 0,
                    f'{model.__name__}.get_instance() created the row as a '
                    f'side effect of a read.',
                )

    def test_the_control_an_existing_row_is_still_returned(self):
        """
        The control that must pass everywhere. A `get_instance` that always
        returned a blank unsaved instance would satisfy every assertion above
        and be catastrophically wrong — a permanently inactive lockdown and an
        empty landing page.
        """
        for model in singleton_models():
            with self.subTest(model=model.__name__):
                model.objects.all().delete()
                cache.clear()
                model.objects.create(pk=SingletonRow.SINGLETON_PK)

                instance = model.get_instance()
                self.assertEqual(instance.pk, SingletonRow.SINGLETON_PK)
                self.assertFalse(
                    instance._state.adding,
                    f'{model.__name__}.get_instance() returned an unsaved '
                    f'placeholder even though the row exists.',
                )

    def test_repeated_reads_of_a_cached_singleton_cost_one_query(self):
        """
        The other half of v3.19.10's reasoning, generalised: dropping the write
        without caching the absence trades one INSERT-once for one uncached
        SELECT on every request forever. That regression passes every test
        above.
        """
        for model in singleton_models():
            if not model.CACHE_KEY:
                continue
            with self.subTest(model=model.__name__):
                model.objects.all().delete()
                cache.clear()
                model.get_instance()  # prime — caches the ABSENCE

                with CaptureQueriesContext(connection) as captured:
                    model.get_instance()
                    model.get_instance()
                    model.get_instance()

                self.assertEqual(
                    len(captured.captured_queries), 0,
                    f'Three warm {model.__name__}.get_instance() calls hit the '
                    f'database {len(captured.captured_queries)} times — the '
                    f'absence is not being cached.',
                )


class AnAbsentSingletonIsStillSaveableTests(TestCase):
    """
    The half v3.19.10 did not have, and it was a live 500.

    Removing the create means `get_instance()` can hand back an **unsaved**
    instance, and Django treats `save(update_fields=[…])` on one as an error:
    it issues an UPDATE, matches nothing, and raises

        DatabaseError: Save with update_fields did not affect any rows.

    Reproduced 08-19-26 against `admin_v2.manage_lockdown`, whose "update
    whitelist" and "update message" actions both use exactly that call.
    """

    def _first_editable_field(self, model):
        for field in model._meta.fields:
            if field.editable and not field.primary_key and field.concrete:
                return field
        return None

    def test_update_fields_save_creates_the_row_when_it_is_absent(self):
        for model in singleton_models():
            field = self._first_editable_field(model)
            if field is None:
                continue
            with self.subTest(model=model.__name__):
                model.objects.all().delete()
                cache.clear()

                instance = model.get_instance()
                try:
                    instance.save(update_fields=[field.name])
                except Exception as exc:      # noqa: BLE001 — the point is the type
                    self.fail(
                        f'{model.__name__}.get_instance().save('
                        f'update_fields=["{field.name}"]) raised '
                        f'{type(exc).__name__}: {exc}\n'
                        f'An absent singleton must be saveable — this is the '
                        f'500 on admin_v2.manage_lockdown.'
                    )
                self.assertEqual(model.objects.count(), 1)

    def test_the_control_update_fields_still_narrows_the_write(self):
        """
        The guard must not turn every save into a full-row write. If it did,
        `save(update_fields=['message'])` would silently persist unrelated
        in-memory edits — a much quieter bug than the 500 it replaces.
        """
        SystemLockdown.objects.all().delete()
        cache.clear()
        SystemLockdown.objects.create(pk=1, message='original', reason='original')

        instance = SystemLockdown.get_instance()
        instance.message = 'changed'
        instance.reason = 'should NOT be written'
        instance.save(update_fields=['message'])

        stored = SystemLockdown.objects.get(pk=1)
        self.assertEqual(stored.message, 'changed')
        self.assertEqual(
            stored.reason, 'original',
            'update_fields no longer narrows the write on a persisted row.',
        )


class TheLockdownConsoleSurvivesAMissingRowTests(TestCase):
    """
    End-to-end, because the finding is a 500 and a unit test on `save()` does
    not prove the page recovered.

    ⚠️ The situation is not exotic: the row is absent on a fresh install, after
    a database restore, and after an admin deletes it — and "set the whitelist
    before activating" is the sequence a person follows during precisely those
    events. This is the emergency-lockdown console.
    """

    def setUp(self):
        cache.clear()
        SystemLockdown.objects.all().delete()
        self.admin = ParliamentUser.objects.create(
            user_id='lockdown-admin', username='lockdown-admin',
            name='Lockdown Admin', member_type='Officer',
            member_status='Active', is_admin=True,
        )
        self.admin.set_password('singleton-test-pass-12345!')
        self.admin.save()
        self.client = Client()

        patcher = patch.object(
            admin_v2_module, 'ALLOWED_USER_IDS', {self.admin.user_id}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

        self.url = reverse('admin_v2_lockdown')

    def test_updating_the_whitelist_with_no_row_does_not_500(self):
        response = self.client.post(self.url, {
            'action': 'update_whitelist',
            'whitelisted_ips': '203.0.113.7, 203.0.113.8',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            SystemLockdown.objects.get(pk=1).whitelisted_ips,
            ['203.0.113.7', '203.0.113.8'],
        )

    def test_updating_the_message_with_no_row_does_not_500(self):
        response = self.client.post(self.url, {
            'action': 'update_message',
            'message': 'Back at 5pm.',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SystemLockdown.objects.get(pk=1).message, 'Back at 5pm.')

    def test_the_control_the_page_still_renders(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)


class TheAnonymousLandingPageIsAReadTests(TestCase):
    """
    The finding as a visitor experiences it. `landing_page` is the only view in
    this application an unauthenticated stranger can reach and have rendered,
    which makes it the one page where "reading writes" is reachable by anyone.
    """

    def setUp(self):
        cache.clear()
        LandingPageContent.objects.all().delete()

    def test_an_anonymous_get_writes_nothing(self):
        with CaptureQueriesContext(connection) as captured:
            response = Client().get('/')

        self.assertEqual(response.status_code, 200)
        offenders = writes_in(captured.captured_queries)
        self.assertEqual(
            offenders, [],
            'GET / wrote to the database:\n  '
            + '\n  '.join(sql[:120] for sql in offenders),
        )
        self.assertEqual(LandingPageContent.objects.count(), 0)

    def test_a_warm_landing_page_does_not_reread_the_content_row(self):
        client = Client()
        client.get('/')
        with CaptureQueriesContext(connection) as captured:
            client.get('/')

        content_reads = [
            query['sql'] for query in captured.captured_queries
            if 'landingpagecontent' in query['sql'].lower()
        ]
        self.assertEqual(
            content_reads, [],
            'The landing content row is read on every anonymous request; the '
            'cache is not being consulted.',
        )

    def test_mutating_the_returned_instance_does_not_poison_the_cache(self):
        """
        ⚠️ NOT HYPOTHETICAL — `landing_page` substitutes photo tags into the
        three HTML fields of the object it just fetched, before rendering. If
        the cache handed out a shared object rather than a copy, that
        substitution would compound on every subsequent request.

        Both LocMem and Redis serialize on `set`, so it does not. This pins the
        property rather than the backend, because the day someone adds a
        cache backend that does not copy is the day this becomes a bug that
        only shows up in production.
        """
        LandingPageContent.objects.create(pk=1, tagline='canonical')

        first = LandingPageContent.get_instance()
        first.tagline = 'MUTATED IN A REQUEST'

        second = LandingPageContent.get_instance()
        self.assertEqual(second.tagline, 'canonical')

    def test_an_officer_edit_is_visible_on_the_next_read(self):
        """
        The invalidation half. A cached row with a 300-second TTL and no
        receiver means an officer edits the landing page, reloads it, sees the
        old text, and edits it again.
        """
        LandingPageContent.objects.create(pk=1, tagline='before')
        self.assertEqual(LandingPageContent.get_instance().tagline, 'before')

        row = LandingPageContent.objects.get(pk=1)
        row.tagline = 'after'
        row.save()

        self.assertEqual(LandingPageContent.get_instance().tagline, 'after')

    def test_deleting_the_row_invalidates_the_cached_copy(self):
        LandingPageContent.objects.create(pk=1, tagline='before')
        LandingPageContent.get_instance()          # cache the row

        LandingPageContent.objects.get(pk=1).delete()

        instance = LandingPageContent.get_instance()
        self.assertTrue(instance._state.adding)
        self.assertNotEqual(instance.tagline, 'before')
