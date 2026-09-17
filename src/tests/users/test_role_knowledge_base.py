"""
Tests for the officer role knowledge base (09-16-26, requested by Mason) —
`RoleKnowledgeBase` / `RoleKnowledgeBaseRevision` in src/models/transitions.py
and the views in src/view/officer/role_knowledge_base.py.

Three properties this suite exists to protect, per the design decisions Mason
made when this was scoped:

1. Read access is open to ANY logged-in member (not officer_required) — he
   explicitly chose "everyone" over "officers only" / "role holders only".
2. Editing is officer_required, same gate as every other page that touches
   `Role` (manage_roles.py, transitions.py).
3. Editing NEVER overwrites — every save creates a new
   RoleKnowledgeBaseRevision, so a past officer's notes cannot be silently
   lost to the next edit (the reasoning Mason has applied to Kai retention,
   applied here to a different kind of institutional memory).

Also covered: the read path must not create a RoleKnowledgeBase row (the
same write-on-read failure mode src/tests/guards/test_singleton_rows.py
guards for the app's true singletons — this model isn't part of that
population since it's per-Role rather than one-row-total, but the same bug
shape applies just as much to a per-key row that gets created by viewing it).
"""
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import (
    ActivityLog, ParliamentUser, Role, RoleKnowledgeBase, RoleKnowledgeBaseRevision,
)


def _make_user(user_id, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=f'User {user_id}', username=user_id, member_type=member_type,
    )
    user.is_active = True
    user.member_status = 'Active'
    user.set_password('kb-test-pass-12345')
    user.save()
    return user


class RoleKnowledgeBaseReadAccessTests(TestCase):
    """Property 1: read access is open to any logged-in member."""

    def setUp(self):
        self.role = Role.objects.create(name='Test Chair', code='TESTKB')
        self.plain_member = _make_user('kb-plain')
        self.officer = _make_user('kb-officer', member_type='Officer')
        self.client = Client()

    def test_a_plain_member_can_view_a_role_with_no_entries_yet(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Nothing has been written here yet')
        self.assertFalse(response.context['can_edit'])

    def test_viewing_an_empty_role_does_not_create_a_row(self):
        """The write-on-read guard. Viewing must never create
        RoleKnowledgeBase as a side effect — see module docstring."""
        self.client.force_login(self.plain_member)
        self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertEqual(RoleKnowledgeBase.objects.count(), 0)

    def test_an_officer_viewing_an_empty_role_also_does_not_create_a_row(self):
        """Same guard from the editor's side — opening the edit form (which
        is part of this same GET) must not create the row either; only an
        actual save should."""
        self.client.force_login(self.officer)
        response = self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertTrue(response.context['can_edit'])
        self.assertEqual(RoleKnowledgeBase.objects.count(), 0)

    def test_a_plain_member_can_read_content_an_officer_wrote(self):
        self.client.force_login(self.officer)
        self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'Contact the national office by March 1st every year.'},
        )
        self.client.logout()
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertContains(response, 'Contact the national office by March 1st every year.')

    def test_history_page_is_also_open_to_any_member(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base_history', args=[self.role.id]))
        self.assertEqual(response.status_code, 200)

    def test_an_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)


class RoleKnowledgeBaseEditAccessTests(TestCase):
    """Property 2: editing is officer_required, same as manage_roles.py."""

    def setUp(self):
        self.role = Role.objects.create(name='Test Chair', code='TESTKB2')
        self.plain_member = _make_user('kb-plain2')
        self.officer = _make_user('kb-officer2', member_type='Officer')
        self.chair = _make_user('kb-chair2', member_type='Chair')
        self.admin = _make_user('kb-admin2', member_type='Member')
        self.admin.is_admin = True
        self.admin.save(update_fields=['is_admin'])
        self.client = Client()

    def test_a_plain_member_cannot_save_an_edit(self):
        self.client.force_login(self.plain_member)
        response = self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'sneaking this in'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(RoleKnowledgeBase.objects.count(), 0)

    def test_an_officer_can_save_an_edit(self):
        self.client.force_login(self.officer)
        response = self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'Officer-written notes.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RoleKnowledgeBaseRevision.objects.count(), 1)

    def test_a_chair_can_save_an_edit(self):
        self.client.force_login(self.chair)
        response = self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'Chair-written notes.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RoleKnowledgeBaseRevision.objects.count(), 1)

    def test_an_admin_can_save_an_edit(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'Admin-written notes.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RoleKnowledgeBaseRevision.objects.count(), 1)

    def test_get_is_not_allowed_on_the_edit_endpoint(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse('edit_role_knowledge_base', args=[self.role.id]))
        self.assertEqual(response.status_code, 405)

    def test_an_edit_is_recorded_to_the_activity_log(self):
        self.client.force_login(self.officer)
        self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role.id]),
            {'content': 'Officer-written notes.'},
        )
        log = ActivityLog.objects.filter(
            metadata__action='edit_role_knowledge_base', metadata__role_id=self.role.id,
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.officer)


class RoleKnowledgeBaseRevisionHistoryTests(TestCase):
    """Property 3: editing never overwrites — every save is a new revision."""

    def setUp(self):
        self.role = Role.objects.create(name='Test Chair', code='TESTKB3')
        self.officer_one = _make_user('kb-officer3a', member_type='Officer')
        self.officer_two = _make_user('kb-officer3b', member_type='Officer')
        self.client = Client()

    def _save(self, user, content):
        self.client.force_login(user)
        self.client.post(reverse('edit_role_knowledge_base', args=[self.role.id]), {'content': content})
        self.client.logout()

    def test_two_edits_create_two_revisions_not_one_overwritten_row(self):
        self._save(self.officer_one, 'First officer\'s notes.')
        self._save(self.officer_two, 'Second officer\'s notes.')
        self.assertEqual(RoleKnowledgeBaseRevision.objects.count(), 2)

    def test_the_current_page_shows_the_latest_content(self):
        self._save(self.officer_one, 'First officer\'s notes.')
        self._save(self.officer_two, 'Second officer\'s notes.')

        self.client.force_login(self.officer_one)
        response = self.client.get(reverse('role_knowledge_base', args=[self.role.id]))
        self.assertContains(response, "Second officer&#x27;s notes.")
        self.assertNotContains(response, "First officer&#x27;s notes.")

    def test_the_first_officers_notes_are_never_lost(self):
        """The property that matters most: the OLD content must still exist
        somewhere in the database and be reachable, even though it's no
        longer what the current page shows."""
        self._save(self.officer_one, 'First officer\'s notes.')
        self._save(self.officer_two, 'Second officer\'s notes.')

        self.assertTrue(
            RoleKnowledgeBaseRevision.objects.filter(content="First officer's notes.").exists(),
            "the first officer's revision must still exist after a later edit",
        )

        self.client.force_login(self.officer_one)
        history = self.client.get(reverse('role_knowledge_base_history', args=[self.role.id]))
        self.assertContains(history, "First officer&#x27;s notes.")
        self.assertContains(history, "Second officer&#x27;s notes.")

    def test_history_is_ordered_newest_first(self):
        self._save(self.officer_one, 'oldest')
        self._save(self.officer_two, 'newest')

        self.client.force_login(self.officer_one)
        response = self.client.get(reverse('role_knowledge_base_history', args=[self.role.id]))
        revisions = list(response.context['revisions'])
        self.assertEqual(revisions[0].content, 'newest')
        self.assertEqual(revisions[1].content, 'oldest')

    def test_each_revision_records_who_edited_it(self):
        self._save(self.officer_one, 'from officer one')
        self._save(self.officer_two, 'from officer two')

        revisions = {r.content: r.edited_by for r in RoleKnowledgeBaseRevision.objects.all()}
        self.assertEqual(revisions['from officer one'], self.officer_one)
        self.assertEqual(revisions['from officer two'], self.officer_two)

    def test_only_one_knowledge_base_row_exists_per_role_regardless_of_edit_count(self):
        """RoleKnowledgeBase is get_or_create'd on every save — repeated
        edits must reuse the same row (OneToOneField to Role), not create a
        second one that would violate the constraint or silently fork."""
        self._save(self.officer_one, 'edit one')
        self._save(self.officer_two, 'edit two')
        self._save(self.officer_one, 'edit three')
        self.assertEqual(RoleKnowledgeBase.objects.filter(role=self.role).count(), 1)
        self.assertEqual(RoleKnowledgeBaseRevision.objects.count(), 3)


class RoleKnowledgeBaseIndexTests(TestCase):
    """
    The member-facing entry point (09-16-26 follow-up: "add the public
    member link to the quick links on home and in the directory"). Without
    this page, "everyone can read" was only true for someone who already had
    a direct URL — a plain member had no page in the app that led here.
    """

    def setUp(self):
        self.role_filled = Role.objects.create(name='Filled Role', code='TESTKB4A')
        self.role_vacant = Role.objects.create(name='Vacant Role', code='TESTKB4B')
        self.holder = _make_user('kb-holder4', member_type='Officer')
        self.holder.roles.add(self.role_filled)
        self.plain_member = _make_user('kb-plain4')
        self.client = Client()

    def test_any_logged_in_member_can_view_the_index(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base_index'))
        self.assertEqual(response.status_code, 200)

    def test_an_anonymous_visitor_is_redirected_to_login(self):
        response = self.client.get(reverse('role_knowledge_base_index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response.url)

    def test_the_index_lists_every_role_with_its_holder(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base_index'))
        self.assertContains(response, 'Filled Role')
        self.assertContains(response, self.holder.name)
        self.assertContains(response, 'Vacant Role')
        self.assertContains(response, 'Vacant')

    def test_the_index_marks_a_role_with_no_entries_as_empty(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base_index'))
        data_by_role = {d['role'].id: d for d in response.context['roles_data']}
        self.assertFalse(data_by_role[self.role_filled.id]['has_content'])
        self.assertFalse(data_by_role[self.role_vacant.id]['has_content'])

    def test_the_index_marks_a_role_with_an_entry_as_written(self):
        self.client.force_login(self.holder)
        self.client.post(
            reverse('edit_role_knowledge_base', args=[self.role_filled.id]),
            {'content': 'Some notes.'},
        )
        response = self.client.get(reverse('role_knowledge_base_index'))
        data_by_role = {d['role'].id: d for d in response.context['roles_data']}
        self.assertTrue(data_by_role[self.role_filled.id]['has_content'])
        self.assertFalse(data_by_role[self.role_vacant.id]['has_content'])

    def test_each_role_links_to_its_own_knowledge_base_page(self):
        self.client.force_login(self.plain_member)
        response = self.client.get(reverse('role_knowledge_base_index'))
        self.assertContains(
            response, reverse('role_knowledge_base', args=[self.role_filled.id]),
        )

    def test_the_holder_query_does_not_scale_with_role_count(self):
        """N+1 guard — role_transitions() already avoids a per-role holder
        query for the same reason; this page must not reintroduce it.

        The first request after login is COLD — this project's test runner
        (src/cache_isolated_runner.py) clears caches between tests, so the
        first hit pays for FeatureFlag/PageToggle/SiteSetting misses that
        have nothing to do with this page. An unwarmed baseline measured
        that cost instead of the page's own — a real trap here, not a
        hypothetical one: it produced a false "N+1" the first time this
        test was written. Warm the cache with a throwaway request first so
        both measurements are on equal footing."""
        self.client.force_login(self.plain_member)
        self.client.get(reverse('role_knowledge_base_index'))  # warm-up, not measured

        with CaptureQueriesContext(connection) as before:
            self.client.get(reverse('role_knowledge_base_index'))
        baseline = len(before.captured_queries)

        for i in range(10):
            Role.objects.create(name=f'Extra Role {i}', code=f'EXTRA{i}')

        with CaptureQueriesContext(connection) as after:
            self.client.get(reverse('role_knowledge_base_index'))
        self.assertEqual(
            len(after.captured_queries), baseline,
            'adding roles changed the query count — the holder lookup is scaling per role',
        )
