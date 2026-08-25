"""
Private legislation drafts (v3.19.0).

WHAT THIS MODULE IS GUARDING
----------------------------
A `LegislationDraft` makes one promise: **nobody but the author sees it until it
is published.** Everything here tests that promise or the publish transition
that ends it.

⚠️ THE LEAK TEST ASSERTS ON RENDERED OUTPUT, NOT ON QUERYSETS, AND THAT IS THE
POINT. `TheDraftDoesNotAppearOnAnyChapterFacingPage` renders every page that
lists legislation and asks whether the draft's title survives anywhere in the
body. It needs no advance list of which querysets exist, so the surface added
next month is covered without anyone remembering to add it here.

That shape is taken from v3.18.5's `TheRedactionCoversEveryRenderedColumnTests`,
which was written because two enumeration guards — one grepping search terms,
one grepping author filters — both missed a leak that was neither. The lesson
recorded then: *a guard is only as wide as its own list; an assertion on output
needs no list.*

EVERY ASSERTION HERE HAS A CONTROL. The other v3.18.x lesson is that an
assertion which cannot distinguish the bug from the fixture is not an assertion
— a first-draft "the draft title is not in the body" passes trivially if the
page 302s to login, or if the fixture never created the draft. So each leak test
is paired with a positive control proving the page rendered and does show the
things it should.
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Legislation, LegislationDraft, Notification, ParliamentUser


PASSWORD = 'draft-test-pass-12345!'


def make_user(uid, name, member_type='Member', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password(PASSWORD)
    user.save()
    return user


class DraftTestCase(TestCase):
    """
    Fixture note, and it is deliberate: the author is a PLAIN MEMBER and the
    officer is somebody else.

    v3.18.4's fixture lesson was that every Kai fixture granted both identity
    flags together, so three of four combinations were never built and the bug
    lived in one of them. The equivalent trap here is making the author an
    officer — that collapses "can draft" and "can publish" into one person and
    the authz split stops being tested at all.
    """

    def setUp(self):
        self.author = make_user('drf-author', 'Andy Author')
        self.other = make_user('drf-other', 'Otto Other')
        self.officer = make_user('drf-officer', 'Olive Officer', member_type='Officer')
        self.officer_author = make_user(
            'drf-off-author', 'Oscar OfficerAuthor', member_type='Officer',
        )

        # Three distinct probe tokens, and the split matters — see
        # TheDraftDoesNotAppearOnAnyChapterFacingPage. The TITLE is what we
        # search for, so a search page legitimately echoes it back and it is
        # useless as a leak probe there. The BODY and NOTES tokens are never
        # submitted as input by any test, so their presence in a response can
        # only mean the draft object itself was rendered.
        self.draft = LegislationDraft.objects.create(
            author=self.author,
            title='SECRETBILLTITLE Establishing A Thing',
            description='SECRETBODYTOKEN — a description long enough to clear the floor.',
            planned_available_at=timezone.now() + timedelta(days=14),
            notes='PRIVATENOTETOKEN — do not show this to anyone.',
        )

    def login(self, user):
        client = Client()
        client.force_login(user)
        return client


# ---------------------------------------------------------------------------
# 1. The leak test — assert on rendered output, not on field names
# ---------------------------------------------------------------------------


class TheDraftDoesNotAppearOnAnyChapterFacingPage(DraftTestCase):
    """
    Render every page that lists legislation, as somebody who is not the author,
    and assert the draft's title appears on none of them.

    This is the test that makes the separate-model decision checkable. If a
    future change moves drafts into the `Legislation` table behind a boolean,
    this suite is what will fail — and it will fail on the surface that forgot
    to filter, not on the one somebody remembered to write a test for.
    """

    #: Pages that render legislation to somebody other than its author. Each is
    #: `(url_name, viewer_attr, echoes_query)`.
    #:
    #: ⚠️ `echoes_query` EXISTS BECAUSE THIS TEST WAS WRONG ON ITS FIRST RUN.
    #:
    #: Every surface is probed with `?q=SECRETBILLTITLE`, so that a page which
    #: *does* search legislation gets a real chance to surface the draft. But
    #: `global_search` renders the query back into its `<input value=...>` and
    #: into *Found N results for "…"* — so the title appeared in the body of a
    #: page whose Legislation section was empty, and the test called it a leak.
    #:
    #: That is this module's own stated rule, walked into: *an assertion that
    #: cannot distinguish the bug from the fixture is not an assertion.* The
    #: title could not distinguish "the draft was rendered" from "the search box
    #: repeated what I typed."
    #:
    #: The fix is not to skip the search page — that is the surface most likely
    #: to leak. It is to probe with tokens that are never submitted as input, so
    #: their presence can only mean the draft object was rendered. The title
    #: check is kept everywhere it is still meaningful.
    SURFACES = [
        ('home', 'other', False),
        ('vote', 'other', False),
        ('passed_legislation', 'other', False),
        ('global_search', 'other', True),
        ('chapter_documents', 'other', False),
        ('view_all_activity', 'officer', False),
    ]

    def test_no_chapter_facing_page_renders_the_draft(self):
        draft_url = f'/legislation/drafts/{self.draft.id}/'

        for url_name, viewer_attr, echoes_query in self.SURFACES:
            viewer = getattr(self, viewer_attr)
            client = self.login(viewer)
            with self.subTest(page=url_name, viewer=viewer.user_id):
                try:
                    url = reverse(url_name)
                except Exception:
                    self.skipTest(f'{url_name} is not routed in this build')
                    continue

                response = client.get(url, {'q': 'SECRETBILLTITLE'})

                # A 302 or a 403 would make the body assertions below pass for
                # entirely the wrong reason. Fail loudly instead — this is the
                # control, and it is the half that v3.18.5 warned about.
                self.assertEqual(
                    response.status_code, 200,
                    f'{url_name} returned {response.status_code} for '
                    f'{viewer.user_id}, so "the draft is absent" proves nothing. '
                    f'Fix the fixture before trusting this test.',
                )

                body = response.content.decode('utf-8', errors='ignore')

                # These three can only appear if the draft OBJECT was rendered.
                # None of them is ever sent as input by any test in this module.
                self.assertNotIn(
                    'SECRETBODYTOKEN', body,
                    f'{url_name} rendered a private draft\'s description to '
                    f'{viewer.user_id}.',
                )
                self.assertNotIn(
                    'PRIVATENOTETOKEN', body,
                    f'{url_name} rendered a draft\'s private notes to {viewer.user_id}.',
                )
                self.assertNotIn(
                    draft_url, body,
                    f'{url_name} rendered a link to a private draft for '
                    f'{viewer.user_id}.',
                )

                # The title is only a valid probe where the page does not repeat
                # the query back to the user.
                if not echoes_query:
                    self.assertNotIn(
                        'SECRETBILLTITLE', body,
                        f'{url_name} rendered a private draft to {viewer.user_id}.',
                    )

    def test_the_search_page_echo_is_an_echo_and_not_a_result(self):
        """
        The assertion that keeps `echoes_query=True` honest.

        Marking a surface as "echoes the query" removes the title check from it,
        which is exactly the kind of exemption that later hides a real leak. So
        this pins down *why* the title appears there: the search term is
        reflected into the form, and the legislation result set is empty.

        If global_search ever does start returning drafts, the body/notes/URL
        probes above catch it — and so does the result-count assertion here.
        """
        client = self.login(self.other)
        response = client.get(reverse('global_search'), {'q': 'SECRETBILLTITLE'})
        self.assertEqual(response.status_code, 200)

        # Assert on the CONTEXT, not the HTML, so a template change cannot mask
        # it. `global_search` builds one `results` dict and only sets the
        # 'legislation' key when the queryset is non-empty (global_search.py:147),
        # so an absent key and an empty list both mean "nothing matched".
        results = response.context['results']
        self.assertFalse(
            results.get('legislation'),
            'global_search returned legislation results for a private draft: '
            f'{results.get("legislation")!r}',
        )

    def test_the_control_the_author_does_see_their_own_draft(self):
        """
        The positive control for the whole class above.

        Without this, every assertion in this file would still pass if
        `LegislationDraft` were never rendered anywhere at all — including if the
        Drafts tab were silently broken. This is the assertion that distinguishes
        "correctly hidden" from "not implemented".
        """
        client = self.login(self.author)
        response = client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertIn('SECRETBILLTITLE', body)

    def test_another_members_my_work_page_does_not_show_it(self):
        """My Work is per-user; the draft panel must be scoped to the viewer."""
        client = self.login(self.other)
        response = client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode('utf-8')
        self.assertNotIn('SECRETBILLTITLE', body)
        # Control: the page rendered its own furniture, so the absence above is
        # about scoping and not about a blank response.
        self.assertIn('My Work', body)

    def test_an_officer_does_not_get_to_see_drafts_either(self):
        """
        Being an officer is not a grant of visibility into somebody's unfinished
        writing. This mirrors the standing v3.16.2 rule that an operational role
        is not a key to everything — stated here because "officers can see
        everything" is the intuition someone will act on later.
        """
        client = self.login(self.officer)
        response = client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('SECRETBILLTITLE', response.content.decode('utf-8'))

    def test_no_legislation_queryset_can_return_a_draft(self):
        """
        The structural claim behind the separate-model decision, asserted
        directly: a draft is not in the Legislation table, so no filter on it —
        however wrong — can produce one.
        """
        self.assertEqual(Legislation.objects.count(), 0)
        self.assertEqual(LegislationDraft.objects.count(), 1)


# ---------------------------------------------------------------------------
# 2. Ownership scoping on every endpoint
# ---------------------------------------------------------------------------


class EveryDraftEndpointIsOwnershipScoped(DraftTestCase):
    """
    `_get_own_draft` filters on `author=request.user` and is the module's only
    read path. These tests are what stop a future view from fetching by pk.

    404 rather than 403 throughout, deliberately: a 403 confirms the row exists,
    and for a private document its existence is part of what is private.
    """

    def test_another_member_cannot_open_the_edit_page(self):
        client = self.login(self.other)
        response = client.get(reverse('edit_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 404)

    def test_another_member_cannot_post_an_edit(self):
        client = self.login(self.other)
        response = client.post(
            reverse('edit_legislation_draft', args=[self.draft.id]),
            {'title': 'HIJACKED', 'description': 'x' * 25, 'vote_mode': 'percentage',
             'required_percentage': '51'},
        )
        self.assertEqual(response.status_code, 404)
        self.draft.refresh_from_db()
        self.assertNotEqual(self.draft.title, 'HIJACKED')

    def test_another_member_cannot_delete_it(self):
        client = self.login(self.other)
        response = client.post(reverse('delete_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(LegislationDraft.objects.filter(pk=self.draft.pk).exists())

    def test_an_officer_cannot_publish_someone_elses_draft(self):
        """
        The two gates are independent and BOTH apply. Being an officer satisfies
        `officer_required`; it does not make you the author. Publishing someone
        else's unfinished bill under your own name is the failure this prevents.
        """
        client = self.login(self.officer)
        response = client.post(reverse('publish_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Legislation.objects.count(), 0)

    def test_the_control_the_author_can_open_their_own_edit_page(self):
        client = self.login(self.author)
        response = client.get(reverse('edit_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('SECRETBILLTITLE', response.content.decode('utf-8'))

    def test_the_control_the_author_can_delete_their_own(self):
        client = self.login(self.author)
        response = client.post(reverse('delete_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LegislationDraft.objects.filter(pk=self.draft.pk).exists())


# ---------------------------------------------------------------------------
# 3. The authz split — drafting is open, publishing is not
# ---------------------------------------------------------------------------


class DraftingIsOpenButPublishingIsOfficerOnly(DraftTestCase):

    def test_a_plain_member_can_create_a_draft(self):
        client = self.login(self.other)
        response = client.post(reverse('create_legislation_draft'), {
            'title': 'A Member Bill',
            'description': 'Long enough to clear the twenty character floor here.',
            'vote_mode': 'percentage',
            'required_percentage': '51',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            LegislationDraft.objects.filter(author=self.other, title='A Member Bill').exists()
        )

    def test_a_plain_member_cannot_publish_their_own_draft(self):
        """
        The deliberate narrowing. A member drafts; an officer presents. If this
        test is what is in the way of a change Mason wants, the fix is to swap
        the decorator on `publish_legislation_draft` — not to loosen this.
        """
        client = self.login(self.author)
        response = client.post(reverse('publish_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Legislation.objects.count(), 0)

    def test_an_officer_can_publish_their_own_draft(self):
        draft = LegislationDraft.objects.create(
            author=self.officer_author,
            title='An Officer Bill',
            description='Long enough to clear the twenty character floor here.',
            planned_available_at=timezone.now() + timedelta(days=3),
        )
        client = self.login(self.officer_author)
        response = client.post(reverse('publish_legislation_draft', args=[draft.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Legislation.objects.filter(title='An Officer Bill').exists())

    def test_the_publish_button_and_the_endpoint_agree(self):
        """
        `_can_publish` drives the button's enabled state and `officer_required`
        drives the endpoint. They are supposed to be the same predicate. A button
        that shows for someone the endpoint refuses is a support ticket; the
        reverse is a feature nobody can find.
        """
        from src.view.legislation_drafts import _can_publish
        self.assertFalse(_can_publish(self.author))
        self.assertFalse(_can_publish(self.other))
        self.assertTrue(_can_publish(self.officer))
        self.assertTrue(_can_publish(make_user('drf-chair', 'Cha Chair', member_type='Chair')))
        self.assertTrue(_can_publish(make_user('drf-adm', 'Ada Admin', is_admin=True)))


# ---------------------------------------------------------------------------
# 4. The publish transition
# ---------------------------------------------------------------------------


class PublishingCopiesTheRightFieldsAndNotTheWrongOnes(DraftTestCase):

    def setUp(self):
        super().setUp()
        self.draft.author = self.officer_author
        self.draft.save(update_fields=['author'])
        self.client_ = self.login(self.officer_author)

    def _publish(self):
        return self.client_.post(
            reverse('publish_legislation_draft', args=[self.draft.id])
        )

    def test_private_notes_are_not_copied_to_the_published_bill(self):
        """
        `notes` is the field that makes a draft worth having, and it is the field
        that must not survive publication. Asserted on the bill's own columns
        rather than on a rendered page, because this is about storage.
        """
        self._publish()
        legislation = Legislation.objects.get()
        self.assertNotIn('PRIVATENOTETOKEN', legislation.description)
        self.assertNotIn('PRIVATENOTETOKEN', legislation.title)
        # And the note survives for its author, which is the other half.
        self.draft.refresh_from_db()
        self.assertIn('PRIVATENOTETOKEN', self.draft.notes)

    def test_voting_stays_closed_until_the_author_opens_it(self):
        """
        `voting_manual_open=True` is forced at publish. A bill that starts
        collecting ballots on a timer nobody watched is the failure the whole
        feature exists to avoid.
        """
        self._publish()
        legislation = Legislation.objects.get()
        self.assertTrue(legislation.voting_manual_open)
        self.assertIsNone(legislation.voting_starts_at)
        self.assertFalse(legislation.voting_has_started())

    def test_the_draft_is_linked_to_the_bill_and_kept(self):
        self._publish()
        self.draft.refresh_from_db()
        legislation = Legislation.objects.get()
        self.assertEqual(self.draft.published_legislation_id, legislation.pk)
        self.assertIsNotNone(self.draft.published_at)
        self.assertTrue(self.draft.is_published)

    def test_a_published_draft_cannot_be_published_twice(self):
        self._publish()
        self._publish()
        self.assertEqual(Legislation.objects.count(), 1)

    def test_a_draft_below_the_publish_floor_is_refused(self):
        thin = LegislationDraft.objects.create(
            author=self.officer_author,
            title='Too Thin',
            description='short',
            planned_available_at=timezone.now() + timedelta(days=1),
        )
        response = self.client_.post(
            reverse('publish_legislation_draft', args=[thin.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Legislation.objects.filter(title='Too Thin').exists())

    def test_a_draft_with_no_date_is_refused(self):
        undated = LegislationDraft.objects.create(
            author=self.officer_author,
            title='Undated Bill',
            description='Long enough to clear the twenty character floor here.',
        )
        response = self.client_.post(
            reverse('publish_legislation_draft', args=[undated.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Legislation.objects.filter(title='Undated Bill').exists())


# ---------------------------------------------------------------------------
# 5. Deferred availability notification
# ---------------------------------------------------------------------------


class TheChapterIsNotifiedWhenTheBillBecomesAvailable(DraftTestCase):
    """
    The timing change in v3.19.0. Notification used to fire when the row was
    saved; it now fires when `available_at` passes, exactly once.
    """

    def setUp(self):
        super().setUp()
        self.draft.author = self.officer_author
        self.draft.save(update_fields=['author'])
        self.client_ = self.login(self.officer_author)

    def test_publishing_a_future_bill_notifies_nobody_yet(self):
        Notification.objects.all().delete()
        self.client_.post(reverse('publish_legislation_draft', args=[self.draft.id]))

        legislation = Legislation.objects.get()
        self.assertFalse(legislation.is_available())
        self.assertIsNone(
            legislation.availability_notified_at,
            'A bill dated two weeks out was stamped as announced at publish.',
        )
        self.assertEqual(
            Notification.objects.filter(source_type='Legislation').count(), 0,
            'Publishing a future-dated bill notified the chapter immediately.',
        )

    def test_the_task_announces_it_once_the_date_passes(self):
        from src.tasks.votes import notify_available_legislation

        self.client_.post(reverse('publish_legislation_draft', args=[self.draft.id]))
        legislation = Legislation.objects.get()

        # Nothing to do while it is still in the future — the control.
        self.assertEqual(notify_available_legislation(), 0)

        Legislation.objects.filter(pk=legislation.pk).update(
            available_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(notify_available_legislation(), 1)

        legislation.refresh_from_db()
        self.assertIsNotNone(legislation.availability_notified_at)
        self.assertGreater(Notification.objects.filter(source_type='Legislation').count(), 0)

    def test_the_task_is_idempotent(self):
        """
        The stamp is the claim. A beat tick that overlaps the previous one, or a
        manual re-run, must not announce a bill twice. This is the assertion that
        makes the conditional-update design worth its extra line over a
        read-then-write.
        """
        from src.tasks.votes import notify_available_legislation

        self.client_.post(reverse('publish_legislation_draft', args=[self.draft.id]))
        Legislation.objects.all().update(available_at=timezone.now() - timedelta(minutes=1))

        self.assertEqual(notify_available_legislation(), 1)
        sent_after_first = Notification.objects.filter(source_type='Legislation').count()

        self.assertEqual(notify_available_legislation(), 0)
        self.assertEqual(
            Notification.objects.filter(source_type='Legislation').count(),
            sent_after_first,
            'A second run of the task re-announced an already-announced bill.',
        )

    def test_the_lookback_window_refuses_ancient_unannounced_bills(self):
        """
        The safety net against a skipped migration or a restored dump. An
        unannounced bill older than the window is left alone rather than pushed
        to every member — the failure mode this guards is "announce four years of
        legislation in one minute", which has no undo.
        """
        from src.tasks.votes import notify_available_legislation

        Legislation.objects.create(
            title='Ancient Bill',
            description='Long enough to clear the twenty character floor here.',
            posted_by=self.officer_author,
            available_at=timezone.now() - timedelta(days=400),
            availability_notified_at=None,
        )
        self.assertEqual(notify_available_legislation(), 0)

    def test_the_control_a_bill_inside_the_window_is_still_announced(self):
        """
        Without this, the test above would pass if the task were broken outright.
        """
        from src.tasks.votes import notify_available_legislation

        Legislation.objects.create(
            title='Recent Bill',
            description='Long enough to clear the twenty character floor here.',
            posted_by=self.officer_author,
            available_at=timezone.now() - timedelta(hours=2),
            availability_notified_at=None,
        )
        self.assertEqual(notify_available_legislation(), 1)


class TheBackfillKeepsHistoricalBillsQuiet(TestCase):
    """
    Migration 0014 stamps every pre-existing bill as announced. Without it the
    first beat tick after deploy treats the whole historical table as
    unannounced.

    This does not run the migration (Django has already applied it for the test
    database); it asserts the property the migration exists to establish, so that
    a future change to the task cannot reintroduce the failure by another route.
    """

    def test_a_bill_stamped_as_announced_is_never_re_announced(self):
        from src.tasks.votes import notify_available_legislation

        author = make_user('drf-hist', 'Hank Historical', member_type='Officer')
        Legislation.objects.create(
            title='Historical Bill',
            description='Long enough to clear the twenty character floor here.',
            posted_by=author,
            available_at=timezone.now() - timedelta(days=2),
            availability_notified_at=timezone.now() - timedelta(days=2),
        )
        self.assertEqual(notify_available_legislation(), 0)
        self.assertEqual(Notification.objects.filter(source_type='Legislation').count(), 0)


# ---------------------------------------------------------------------------
# 6. v3.19.4 — who owns the file
# ---------------------------------------------------------------------------


class ADraftAttachmentHasExactlyOneLifetime(TestCase):
    """
    v3.19.4 — every draft attachment is deleted by exactly one thing, and no
    code path leaves one behind.

    ⚠️ WHY THIS NEEDED FIXING. Django has not deleted files on model delete
    since 1.3, and that default is right in general: a file can be shared
    between rows and an ORM delete is a bad place to discover it. For
    `LegislationDraft` the premise stopped holding at v3.19.3 — the attachment
    is at a uuid under `legislation_drafts/`, referenced by exactly one row, and
    publish now COPIES it into `legislation_docs/` rather than re-pointing at
    it. So it has one owner, and until v3.19.4 nothing ever exercised that
    ownership:

      * deleting a draft left the blob behind, unreferenced and unnameable;
      * publishing left a SECOND copy behind, because the copy that made the
        published bill safe never said what happened to the original.

    The v3.19.3 fix removed a footgun (two rows, one path) and replaced it with
    an orphan set with no owner. These tests are the ownership.

    ⚠️ v3.19.5 — AND FOR ONE RELEASE THIS CLASS'S NAME WAS A CLAIM, NOT A FACT.
    v3.19.4 covered delete and publish and stopped there, so **replacing** or
    **clearing** an attachment from the edit form still orphaned the old file —
    the two things a member does far more often than either of the covered paths.
    A test class named for a property should assert the property; the four
    `replace and clear` tests below are the rest of it.

    ⚠️ `MEDIA_ROOT` is overridden per test into a temporary directory. Without
    that these tests write into the real one and — worse — a bug in the guard
    would delete out of the developer's actual media folder.
    """

    def setUp(self):
        import shutil
        import tempfile

        self.media = tempfile.mkdtemp(prefix='parliament-draft-test-')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

        self.author = make_user('drf-file-auth', 'Fiona Fileauthor', member_type='Officer')

    def _attach(self, draft, body=b'%PDF-1.4 pretend'):
        """Give `draft` a real file on disk and return its stored name."""
        from django.core.files.base import ContentFile

        draft.document.save('Dues Restructuring Amendment.pdf', ContentFile(body), save=True)
        return draft.document.name

    def _make_draft(self, **kw):
        defaults = dict(
            author=self.author,
            title='A Bill With An Attachment',
            description='Long enough to clear the twenty character publish floor.',
            planned_available_at=timezone.now() + timedelta(days=7),
        )
        defaults.update(kw)
        return LegislationDraft.objects.create(**defaults)

    # ───────────────────────────────────────────────────────────── delete

    def test_deleting_a_draft_deletes_its_file(self):
        """**Fails against the v3.19.3 tree**, where the blob was left behind."""
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            name = self._attach(draft)
            path = os.path.join(self.media, name)
            self.assertTrue(os.path.isfile(path), 'fixture did not write a file')

            draft.delete()

            self.assertFalse(os.path.isfile(path))

    def test_deleting_a_draft_with_no_attachment_is_not_an_error(self):
        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            self._make_draft().delete()
            self.assertEqual(LegislationDraft.objects.count(), 0)

    def test_a_missing_file_does_not_break_the_delete(self):
        """
        The row is what the member asked to remove. A cleanup that raises would
        fail a delete that has already succeeded, so a file that is already gone
        must be the desired end state and not an error.
        """
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            name = self._attach(draft)
            os.remove(os.path.join(self.media, name))

            draft.delete()                       # must not raise
            self.assertEqual(LegislationDraft.objects.count(), 0)

    # ──────────────────────────────────────────────────────────── publish

    def test_publishing_removes_the_private_original_and_keeps_the_copy(self):
        """
        The whole point of the v3.19.3 copy is that the published bill's bytes
        live somewhere chapter-readable. The private original is then redundant,
        and v3.19.3 left it on disk forever.

        ⚠️ `captureOnCommitCallbacks` is required: the unlink is deferred with
        `transaction.on_commit`, deliberately, so that turning on
        `ATOMIC_REQUESTS` cannot turn it into "delete the only copy on a later
        rollback". Under `TestCase` nothing ever commits, so without this the
        callback silently never runs and the test passes for the wrong reason.
        """
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            private_name = self._attach(draft)
            private_path = os.path.join(self.media, private_name)

            client = Client()
            client.force_login(self.author)
            with self.captureOnCommitCallbacks(execute=True):
                client.post(reverse('publish_legislation_draft', args=[draft.id]))

            legislation = Legislation.objects.get()

            self.assertTrue(
                legislation.document, 'the published bill must have a document',
            )
            self.assertTrue(
                os.path.isfile(os.path.join(self.media, legislation.document.name)),
                'the published copy must exist on disk',
            )
            self.assertFalse(
                os.path.isfile(private_path),
                'the redundant private original must be gone',
            )

            draft.refresh_from_db()
            self.assertFalse(draft.document, 'the draft must no longer claim a file')

    def test_the_published_copy_is_not_in_the_private_directory(self):
        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            self._attach(draft)

            client = Client()
            client.force_login(self.author)
            with self.captureOnCommitCallbacks(execute=True):
                client.post(reverse('publish_legislation_draft', args=[draft.id]))

            name = Legislation.objects.get().document.name
            self.assertNotIn('legislation_drafts/', name)
            self.assertIn('legislation_docs/', name)

    def test_the_published_copy_carries_the_authors_filename(self):
        """
        The uuid is a storage detail and must never reach a member's download.
        `document_original_name` is what publish names the copy with.
        """
        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft(document_original_name='Dues Restructuring Amendment.pdf')
            self._attach(draft)

            client = Client()
            client.force_login(self.author)
            with self.captureOnCommitCallbacks(execute=True):
                client.post(reverse('publish_legislation_draft', args=[draft.id]))

            name = Legislation.objects.get().document.name
            self.assertIn('dues-restructuring-amendment', name)

    def test_the_bytes_survive_the_move(self):
        """
        A test that only checks paths would pass on a zero-byte copy. The point
        of the exercise is that the chapter can read the bill.
        """
        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            self._attach(draft, body=b'%PDF-1.4 UNIQUEBODYBYTES')

            client = Client()
            client.force_login(self.author)
            with self.captureOnCommitCallbacks(execute=True):
                client.post(reverse('publish_legislation_draft', args=[draft.id]))

            with Legislation.objects.get().document.open('rb') as fh:
                self.assertIn(b'UNIQUEBODYBYTES', fh.read())

    def test_deleting_a_published_draft_does_not_touch_the_bill(self):
        """
        ⚠️ THE ONE THAT MAKES THE `post_delete` RECEIVER SAFE.

        A published draft's row is still deletable, and its receiver still
        fires. Because publish cleared `draft.document`, there is nothing for it
        to unlink — so the bill's own copy is untouched. If publish ever stops
        clearing the field, this fails, which is the point: the receiver and the
        publish path are two halves of one invariant.
        """
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            self._attach(draft)

            client = Client()
            client.force_login(self.author)
            with self.captureOnCommitCallbacks(execute=True):
                client.post(reverse('publish_legislation_draft', args=[draft.id]))

            legislation = Legislation.objects.get()
            bill_path = os.path.join(self.media, legislation.document.name)

            draft.refresh_from_db()
            draft.delete()

            self.assertTrue(
                os.path.isfile(bill_path),
                'Deleting the draft must never remove the published bill\'s document.',
            )

    # ──────────────────────────────────────────── replace and clear (v3.19.5)
    #
    # ⚠️ THE TWO PATHS v3.19.4 DID NOT COVER, AND THE REASON THIS CLASS'S NAME
    # WAS A CLAIM RATHER THAN A FACT.
    #
    # v3.19.4 handled delete (a `post_delete` receiver) and publish (unlink the
    # private original after commit). Both are correct. Between them they covered
    # neither thing a member actually does from the edit form. Replacing an
    # attachment writes a fresh uuid and overwrites the field; clearing it empties
    # the field. Both leave the previous file on disk with nothing in the database
    # naming it — the same orphan the release existed to abolish, reached through
    # the edit button instead of the delete button.
    #
    # The rule: **enumerate the ways a reference can END, not the ways you have
    # already written code for.** Delete is the one that looks like cleanup, so it
    # is the one that gets cleanup written for it.

    def _post_edit(self, draft, **extra):
        """POST the edit form as the author, with the fields it requires."""
        client = Client()
        client.force_login(self.author)
        data = {
            'title': draft.title,
            'description': draft.description,
            'notes': draft.notes or '',
            'vote_mode': draft.vote_mode,
            'required_percentage': draft.required_percentage,
            'planned_available_at': draft.planned_available_at.strftime('%Y-%m-%dT%H:%M'),
        }
        data.update(extra)
        with self.captureOnCommitCallbacks(execute=True):
            return client.post(reverse('edit_legislation_draft', args=[draft.id]), data)

    def test_replacing_an_attachment_removes_the_old_file(self):
        """**Fails against the v3.19.4 tree**, where the old blob stayed forever."""
        import os

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            old_name = self._attach(draft, b'%PDF-1.4 the first one')
            old_path = os.path.join(self.media, old_name)
            self.assertTrue(os.path.isfile(old_path), 'fixture did not write a file')

            self._post_edit(draft, document=SimpleUploadedFile(
                'Replacement.pdf', b'%PDF-1.4 the second one',
                content_type='application/pdf'))

            draft.refresh_from_db()
            new_name = draft.document.name

            self.assertNotEqual(new_name, old_name, 'the fixture did not replace anything')
            self.assertTrue(os.path.isfile(os.path.join(self.media, new_name)),
                            'the replacement must exist')
            self.assertFalse(os.path.isfile(old_path),
                             'the replaced file was orphaned on disk')

    def test_clearing_an_attachment_removes_the_file(self):
        """**Fails against the v3.19.4 tree.**"""
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            name = self._attach(draft)
            path = os.path.join(self.media, name)

            # `ClearableFileInput` signals a clear with `<field>-clear`.
            self._post_edit(draft, **{'document-clear': 'on'})

            draft.refresh_from_db()
            self.assertFalse(draft.document, 'the fixture did not clear the field')
            self.assertFalse(os.path.isfile(path),
                             'the cleared file was orphaned on disk')

    def test_editing_without_touching_the_attachment_keeps_the_file(self):
        """
        ⚠️ THE CONTROL, and the assertion that stops the fix from being a bug.

        Every test above passes against an implementation that unlinks on every
        save. This one does not. A member renaming a draft must not lose the
        document they uploaded two weeks ago — which is the same failure mode
        v3.19.3's `changed_data` guard was written for one field over, and the
        reason the unlink compares stored NAMES rather than trusting
        `changed_data` alone.
        """
        import os

        from django.test import override_settings

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            name = self._attach(draft)
            path = os.path.join(self.media, name)

            self._post_edit(draft, title='A Bill With A New Title')

            draft.refresh_from_db()
            self.assertEqual(draft.title, 'A Bill With A New Title',
                             'the fixture did not actually edit anything')
            self.assertEqual(draft.document.name, name)
            self.assertTrue(os.path.isfile(path),
                            'an unrelated edit deleted the attachment')

    def test_saving_the_same_form_twice_does_not_unlink_the_new_file(self):
        """
        The form keeps its own record of which file it loaded. If that record is
        not advanced after a save, a second `save()` on the same bound form
        schedules the unlink again — and by then `previous` names the file the
        row now points at, so the fix would delete the live attachment.
        """
        import os

        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings

        from src.forms import LegislationDraftForm

        with override_settings(MEDIA_ROOT=self.media):
            draft = self._make_draft()
            self._attach(draft, b'%PDF-1.4 original')

            form = LegislationDraftForm(
                data={
                    'title': draft.title,
                    'description': draft.description,
                    'notes': '',
                    'vote_mode': draft.vote_mode,
                    'required_percentage': draft.required_percentage,
                    'planned_available_at': draft.planned_available_at.strftime('%Y-%m-%dT%H:%M'),
                },
                files={'document': SimpleUploadedFile(
                    'Replacement.pdf', b'%PDF-1.4 replacement',
                    content_type='application/pdf')},
                instance=draft,
            )
            self.assertTrue(form.is_valid(), form.errors)

            with self.captureOnCommitCallbacks(execute=True):
                form.save()
            with self.captureOnCommitCallbacks(execute=True):
                form.save()

            draft.refresh_from_db()
            self.assertTrue(
                os.path.isfile(os.path.join(self.media, draft.document.name)),
                'the second save deleted the file the row points at',
            )


class TheUnlinkGuardRefusesToLeaveMediaRoot(TestCase):
    """
    `delete_draft_document_file`'s guard, tested directly.

    ⚠️ WHY THE GUARD EXISTS AND WHY IT IS WORTH ITS OWN CLASS.
    `DualLocationStorage.path()` falls back to `BASE_DIR/exportable_media/` when
    a name is absent from `MEDIA_ROOT` — and `exportable_media/` is **committed
    to a public repo by design** (CLAUDE.md's standing disposition) and contains
    the chapter's governing documents. So a plain `document.delete()` on a row
    whose file had already gone missing could resolve into the public directory
    and delete a governing document out of the working tree.

    Nothing in the current code can reach that state. That is exactly the kind
    of fact that stops being true later, which is why the guard is unconditional
    and why it is tested against inputs the application cannot currently
    produce.
    """

    def setUp(self):
        import shutil
        import tempfile

        self.media = tempfile.mkdtemp(prefix='parliament-guard-test-')
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def test_it_deletes_a_file_inside_media_root(self):
        import os

        from django.test import override_settings

        from src.models.legislation import delete_draft_document_file

        os.makedirs(os.path.join(self.media, 'legislation_drafts'))
        target = os.path.join(self.media, 'legislation_drafts', 'abc.pdf')
        with open(target, 'wb') as fh:
            fh.write(b'draft')

        with override_settings(MEDIA_ROOT=self.media):
            self.assertTrue(delete_draft_document_file('legislation_drafts/abc.pdf'))
        self.assertFalse(os.path.exists(target))

    def test_it_refuses_to_traverse_out_of_media_root(self):
        import os

        from django.test import override_settings

        from src.models.legislation import delete_draft_document_file

        outside_dir = os.path.join(self.media, '..', 'guard-outside')
        os.makedirs(outside_dir, exist_ok=True)
        self.addCleanup(
            lambda: os.path.exists(os.path.join(outside_dir, 'Kai-Binder.pdf'))
            and os.remove(os.path.join(outside_dir, 'Kai-Binder.pdf'))
        )
        victim = os.path.join(outside_dir, 'Kai-Binder.pdf')
        with open(victim, 'wb') as fh:
            fh.write(b'PUBLIC GOVERNING DOCUMENT')

        with override_settings(MEDIA_ROOT=self.media):
            for name in (
                '../guard-outside/Kai-Binder.pdf',
                'legislation_drafts/../../guard-outside/Kai-Binder.pdf',
                victim,                                    # absolute path
            ):
                self.assertFalse(
                    delete_draft_document_file(name),
                    f'{name!r} escaped MEDIA_ROOT',
                )

        self.assertTrue(os.path.isfile(victim), 'the file outside MEDIA_ROOT survived')

    def test_an_empty_or_missing_name_is_false_not_an_error(self):
        from django.test import override_settings

        from src.models.legislation import delete_draft_document_file

        with override_settings(MEDIA_ROOT=self.media):
            self.assertFalse(delete_draft_document_file(''))
            self.assertFalse(delete_draft_document_file(None))
            self.assertFalse(delete_draft_document_file('legislation_drafts/nope.pdf'))

    def test_a_directory_is_not_deleted(self):
        import os

        from django.test import override_settings

        from src.models.legislation import delete_draft_document_file

        os.makedirs(os.path.join(self.media, 'legislation_drafts'))
        with override_settings(MEDIA_ROOT=self.media):
            self.assertFalse(delete_draft_document_file('legislation_drafts'))
        self.assertTrue(os.path.isdir(os.path.join(self.media, 'legislation_drafts')))
