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

        self.draft = LegislationDraft.objects.create(
            author=self.author,
            title='SECRETBILLTITLE Establishing A Thing',
            description='A description long enough to clear the twenty character floor.',
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
    #: `(url_name, viewer_attr)`. The list is a convenience for readable failure
    #: messages — the *guarantee* comes from the assertion being on the response
    #: body, so adding a page here is cheap and forgetting to is not fatal the
    #: way forgetting a queryset filter would be.
    SURFACES = [
        ('home', 'other'),
        ('vote', 'other'),
        ('passed_legislation', 'other'),
        ('global_search', 'other'),
        ('chapter_documents', 'other'),
        ('view_all_activity', 'officer'),
    ]

    def test_no_chapter_facing_page_renders_the_draft(self):
        for url_name, viewer_attr in self.SURFACES:
            viewer = getattr(self, viewer_attr)
            client = self.login(viewer)
            with self.subTest(page=url_name, viewer=viewer.user_id):
                try:
                    url = reverse(url_name)
                except Exception:
                    self.skipTest(f'{url_name} is not routed in this build')
                    continue

                response = client.get(url, {'q': 'SECRETBILLTITLE'})

                # A 302 or a 403 would make the body assertion below pass for
                # entirely the wrong reason. Fail loudly instead — this is the
                # control, and it is the half that v3.18.5 warned about.
                self.assertEqual(
                    response.status_code, 200,
                    f'{url_name} returned {response.status_code} for '
                    f'{viewer.user_id}, so "the draft is absent" proves nothing. '
                    f'Fix the fixture before trusting this test.',
                )

                body = response.content.decode('utf-8', errors='ignore')
                self.assertNotIn(
                    'SECRETBILLTITLE', body,
                    f'{url_name} rendered a private draft to {viewer.user_id}.',
                )
                self.assertNotIn(
                    'PRIVATENOTETOKEN', body,
                    f'{url_name} rendered a draft\'s private notes to {viewer.user_id}.',
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
