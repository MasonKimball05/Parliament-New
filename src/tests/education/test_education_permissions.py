"""
v3.32.0 — Mason: "can we also add an education permissions dashboard so the
admin of the committee can set what permissions the members have whether
it's none, some permissions; view and grade tasks (2 separate perms), or
all permissions where they can make tasks and whatnot. Kai has a version
of this already."

New `EducationMemberPermission` model (mirrors `KaiMemberPermission`) with
three additive booleans — `can_view_submissions`, `can_grade_submissions`,
`can_manage_tasks` — granted per member by an education chair on the new
`manage_education_permissions` page.

⚠️ THIS REPLACES THE PRE-EXISTING ACCESS MODEL. Before this release, any
chapter officer (`request.user.is_officer`) had full access to the entire
education dashboard regardless of committee membership; only a couple of
destructive actions (publish toggle, delete meeting) were chair-gated.
Confirmed with Mason before building (he chose "full Kai-style lockdown"
over an additive-only option): a real committee chair or a site admin still
gets automatic full access, but every other officer now needs an explicit
`EducationMemberPermission` grant for each of the three tiers, or has NO
access to the education dashboard at all. `test_quiz_grading_visibility.py`
(v3.31.7) was updated in the same change — its "any officer" assumption
predates this model and no longer holds.

Grading scope is deliberately narrow: `can_grade_submissions` covers only
quiz answer marking/scoring/pass-incomplete (confirmed with Mason). Meeting
attendance marking and absence-request review fall under `can_manage_tasks`
instead, alongside task/meeting/quiz-question/page-restriction management —
see the comments on `education_meeting_attendance` and
`education_review_absence` in `src/view/committee/education.py`.

Two chair-only actions that existed before this release —
`education_toggle_task_published` and `education_delete_meeting` — are
folded into `can_manage_tasks` here rather than kept as a separate
chair-only sub-tier, since the 3-permission model has no room for a fourth
level.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import (
    PledgeTask, PledgeTaskQuestion, PledgeTaskCompletion, PledgeQuizAnswer,
    EducationMeeting, EducationAbsenceRequest, Event, Role,
    EducationMemberPermission,
)
from src.tests.education._fixtures import EducationFixtureMixin, make_user
from src.view.committee.education import _get_education_access
from src.signals import reset_education_permissions_on_role_change


class GetEducationAccessTests(EducationFixtureMixin, TestCase):
    """Direct unit tests of `_get_education_access`'s branch logic."""

    def setUp(self):
        self.build()

    def test_a_real_chair_gets_full_access(self):
        access = _get_education_access(self.chair, self.committee)
        self.assertTrue(access['is_chair'])
        self.assertTrue(access['is_full_access'])
        self.assertTrue(access['can_view_submissions'])
        self.assertTrue(access['can_grade_submissions'])
        self.assertTrue(access['can_manage_tasks'])

    def test_a_site_admin_gets_full_access_without_a_permission_row(self):
        admin = make_user('9010', 'Site Admin', member_type='Officer', is_admin=True)
        access = _get_education_access(admin, self.committee)
        self.assertFalse(access['is_chair'])
        self.assertTrue(access['is_full_access'])
        self.assertTrue(access['can_manage_tasks'])

    def test_an_ungranted_member_gets_nothing(self):
        access = _get_education_access(self.brother, self.committee)
        self.assertFalse(access['is_chair'])
        self.assertFalse(access['is_full_access'])
        self.assertFalse(access['can_view_submissions'])
        self.assertFalse(access['can_grade_submissions'])
        self.assertFalse(access['can_manage_tasks'])

    def test_an_explicit_grant_returns_exactly_what_was_granted(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother,
            can_view_submissions=True, can_grade_submissions=False, can_manage_tasks=False,
        )
        access = _get_education_access(self.brother, self.committee)
        self.assertFalse(access['is_full_access'])
        self.assertTrue(access['can_view_submissions'])
        self.assertFalse(access['can_grade_submissions'])
        self.assertFalse(access['can_manage_tasks'])

    def test_a_chair_row_is_never_created_but_still_reports_full_access(self):
        """
        A chair does not need an explicit EducationMemberPermission row —
        `_get_education_access` short-circuits on `committee.chairs` before
        ever querying the permission table.
        """
        self.assertFalse(
            EducationMemberPermission.objects.filter(committee=self.committee, user=self.chair).exists()
        )
        access = _get_education_access(self.chair, self.committee)
        self.assertTrue(access['is_full_access'])


class EducationMemberPermissionModelTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def test_defaults_are_all_false(self):
        perm = EducationMemberPermission.objects.create(committee=self.committee, user=self.brother)
        self.assertFalse(perm.can_view_submissions)
        self.assertFalse(perm.can_grade_submissions)
        self.assertFalse(perm.can_manage_tasks)

    def test_one_row_per_committee_per_user(self):
        EducationMemberPermission.objects.create(committee=self.committee, user=self.brother)
        with self.assertRaises(Exception):
            EducationMemberPermission.objects.create(committee=self.committee, user=self.brother)

    def test_str_includes_committee_and_member_name(self):
        perm = EducationMemberPermission.objects.create(committee=self.committee, user=self.brother)
        self.assertIn(self.committee.name, str(perm))
        self.assertIn(self.brother.name, str(perm))


class ViewTierGatingTests(EducationFixtureMixin, TestCase):
    """The base gate: reaching ANY education page needs SOME permission."""

    def setUp(self):
        self.build()
        self.home_url = reverse('education_home', args=[self.committee.code])

    def test_an_ungranted_member_is_404d_off_the_dashboard(self):
        client = Client()
        client.force_login(self.brother)
        self.assertEqual(client.get(self.home_url).status_code, 404)

    def test_view_only_grant_can_open_the_dashboard(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_view_submissions=True,
        )
        client = Client()
        client.force_login(self.brother)
        self.assertEqual(client.get(self.home_url).status_code, 200)

    def test_grade_only_grant_can_also_open_the_dashboard(self):
        """Any of the three permissions is enough to clear the base gate."""
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        client = Client()
        client.force_login(self.brother)
        self.assertEqual(client.get(self.home_url).status_code, 200)

    def test_manage_only_grant_can_also_open_the_dashboard(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        client = Client()
        client.force_login(self.brother)
        self.assertEqual(client.get(self.home_url).status_code, 200)


class ManageTierGatingTests(EducationFixtureMixin, TestCase):
    """`can_manage_tasks` gates task/meeting/quiz-question/restriction writes."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='A Task')

    def _client_as(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_view_only_member_cannot_add_a_task(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_view_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_add_task', args=[self.committee.code]),
            {'title': 'New Task'},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(PledgeTask.objects.filter(title='New Task').exists())

    def test_manage_grant_can_add_a_task(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_add_task', args=[self.committee.code]),
            {'title': 'New Task'},
        )
        self.assertIn(resp.status_code, (200, 302))
        self.assertTrue(PledgeTask.objects.filter(title='New Task').exists())

    def test_grade_only_member_cannot_delete_a_task(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_delete_task', args=[self.committee.code, self.task.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 403)
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_active)

    def test_manage_grant_can_edit_task_form_page(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).get(
            reverse('education_edit_task', args=[self.committee.code, self.task.pk]),
        )
        self.assertEqual(resp.status_code, 200)

    def test_view_only_member_is_404d_off_the_edit_task_page(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_view_submissions=True,
        )
        resp = self._client_as(self.brother).get(
            reverse('education_edit_task', args=[self.committee.code, self.task.pk]),
        )
        self.assertEqual(resp.status_code, 404)

    def test_manage_grant_can_publish_toggle_a_task(self):
        """
        ⚠️ v3.32.0 — was chair-only before this release; now `can_manage_tasks`
        covers it, since the model has no separate chair-only sub-tier.
        """
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_toggle_task_published', args=[self.committee.code, self.task.pk]),
        )
        self.assertEqual(resp.status_code, 200)


class GradeTierGatingTests(EducationFixtureMixin, TestCase):
    """`can_grade_submissions` gates quiz answer marking and completion status."""

    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.question = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='When were we founded?')
        self.answer = PledgeQuizAnswer.objects.create(
            question=self.question, pledge=self.pledge, answer_text='1839',
        )

    def _client_as(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_manage_only_member_cannot_mark_an_answer(self):
        """
        Manage does not imply grade — the two are independent booleans, so a
        manage-only grant should not let someone mark a quiz answer.
        """
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_mark_answer', args=[self.committee.code, self.quiz.pk, self.answer.pk]),
            {'verdict': 'correct'},
        )
        self.assertEqual(resp.status_code, 403)
        self.answer.refresh_from_db()
        self.assertIsNone(self.answer.is_correct)

    def test_grade_grant_can_mark_an_answer(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_mark_answer', args=[self.committee.code, self.quiz.pk, self.answer.pk]),
            {'verdict': 'correct'},
        )
        self.assertEqual(resp.status_code, 200)
        self.answer.refresh_from_db()
        self.assertTrue(self.answer.is_correct)

    def test_grade_grant_can_toggle_completion_status(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_toggle_completion', args=[self.committee.code, self.quiz.pk, self.pledge.pk]),
            {'set_status': 'completed'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        comp = PledgeTaskCompletion.objects.get(task=self.quiz, pledge=self.pledge)
        self.assertEqual(comp.status, 'completed')

    def test_view_only_member_cannot_toggle_completion_status(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_view_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_toggle_completion', args=[self.committee.code, self.quiz.pk, self.pledge.pk]),
            {'set_status': 'completed'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 403)


class AttendanceAndAbsenceAreManageTierTests(EducationFixtureMixin, TestCase):
    """
    Confirms attendance-marking and absence-review require `can_manage_tasks`,
    NOT `can_grade_submissions` — Mason confirmed grading scope is quiz
    grading only, so a grade-only member should be denied both.
    """

    def setUp(self):
        self.build()
        event = Event.objects.create(
            title='Pledge Meeting', date_time='2026-01-01T18:00:00Z', created_by=self.chair,
        )
        self.meeting = EducationMeeting.objects.create(committee=self.committee, event=event)
        self.absence = EducationAbsenceRequest.objects.create(
            meeting=self.meeting, pledge=self.pledge, reason='Sick',
        )

    def _client_as(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_grade_only_member_cannot_mark_attendance(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        resp = self._client_as(self.brother).get(
            reverse('education_meeting_attendance', args=[self.committee.code, self.meeting.pk]),
        )
        self.assertEqual(resp.status_code, 404)

    def test_manage_grant_can_mark_attendance(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).get(
            reverse('education_meeting_attendance', args=[self.committee.code, self.meeting.pk]),
        )
        self.assertEqual(resp.status_code, 200)

    def test_grade_only_member_cannot_review_an_absence(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_grade_submissions=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_review_absence', args=[self.committee.code, self.absence.pk]),
            {'decision': 'approved'},
        )
        self.assertEqual(resp.status_code, 403)
        self.absence.refresh_from_db()
        self.assertEqual(self.absence.status, 'pending')

    def test_manage_grant_can_review_an_absence(self):
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_manage_tasks=True,
        )
        resp = self._client_as(self.brother).post(
            reverse('education_review_absence', args=[self.committee.code, self.absence.pk]),
            {'decision': 'approved'},
        )
        self.assertIn(resp.status_code, (200, 302))
        self.absence.refresh_from_db()
        self.assertEqual(self.absence.status, 'approved')


class ManageEducationPermissionsPageTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.url = reverse('manage_education_permissions', args=[self.committee.code])
        self.update_url = lambda uid: reverse(
            'update_education_member_permission', args=[self.committee.code, uid],
        )
        self.reset_url = reverse('reset_education_permissions', args=[self.committee.code])

    def _client_as(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_chair_can_open_the_page(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_chair_row_renders_checked_and_disabled(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'education-row-{self.chair.pk}', html)
        self.assertIn('Chair</span>', html)

    def test_non_chair_non_admin_is_redirected_away(self):
        resp = self._client_as(self.brother).get(self.url, follow=True)
        # Redirects to committee_home with an error message rather than a 403/404 —
        # matches manage_kai_permissions' own choice.
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(f'education-row-{self.brother.pk}', resp.content.decode())

    def test_admin_can_open_the_page_without_being_a_committee_member(self):
        admin = make_user('9011', 'Site Admin', member_type='Officer', is_admin=True)
        resp = self._client_as(admin).get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_chair_can_grant_a_permission_to_a_member(self):
        resp = self.client.post(self.update_url(self.brother.pk), {
            'can_view_submissions': 'true',
            'can_grade_submissions': 'false',
            'can_manage_tasks': 'false',
        })
        self.assertEqual(resp.status_code, 200)
        perm = EducationMemberPermission.objects.get(committee=self.committee, user=self.brother)
        self.assertTrue(perm.can_view_submissions)
        self.assertFalse(perm.can_manage_tasks)
        self.assertEqual(perm.granted_by, self.chair)

    def test_granting_all_three_matches_mason_s_all_permissions_tier(self):
        resp = self.client.post(self.update_url(self.brother.pk), {
            'can_view_submissions': 'true',
            'can_grade_submissions': 'true',
            'can_manage_tasks': 'true',
        })
        self.assertEqual(resp.status_code, 200)
        access = _get_education_access(self.brother, self.committee)
        self.assertTrue(access['can_view_submissions'])
        self.assertTrue(access['can_grade_submissions'])
        self.assertTrue(access['can_manage_tasks'])

    def test_a_non_chair_cannot_grant_permissions(self):
        resp = self._client_as(self.brother).post(self.update_url(self.pledge.pk), {
            'can_view_submissions': 'true',
        })
        self.assertEqual(resp.status_code, 403)

    def test_cannot_create_a_redundant_permission_row_for_a_chair(self):
        resp = self.client.post(self.update_url(self.chair.pk), {'can_view_submissions': 'true'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(
            EducationMemberPermission.objects.filter(committee=self.committee, user=self.chair).exists()
        )

    def test_reset_wipes_all_rows(self):
        EducationMemberPermission.objects.create(committee=self.committee, user=self.brother, can_view_submissions=True)
        EducationMemberPermission.objects.create(committee=self.committee, user=self.pledge, can_grade_submissions=True)
        resp = self.client.post(self.reset_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(EducationMemberPermission.objects.filter(committee=self.committee).count(), 0)

    def test_a_non_chair_cannot_reset(self):
        resp = self._client_as(self.brother).post(self.reset_url)
        self.assertEqual(resp.status_code, 403)

    def test_no_inline_event_handlers(self):
        import re
        html = self.client.get(self.url).content.decode()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))


class RoleChangeResetTests(EducationFixtureMixin, TestCase):
    """
    Mirrors `reset_kai_permissions_on_role_change` — every
    `EducationMemberPermission` row for a committee is wiped when the exec
    role tied to that committee (`committee.role`) changes hands.
    """

    def setUp(self):
        self.build()
        self.role = Role.objects.create(name='VP Education', code='VP_EDUCATION')
        self.committee.role = self.role
        self.committee.save(update_fields=['role'])
        EducationMemberPermission.objects.create(
            committee=self.committee, user=self.brother, can_view_submissions=True,
        )

    def test_matching_role_change_wipes_permissions(self):
        self.assertEqual(EducationMemberPermission.objects.filter(committee=self.committee).count(), 1)
        reset_education_permissions_on_role_change([self.role.pk])
        self.assertEqual(EducationMemberPermission.objects.filter(committee=self.committee).count(), 0)

    def test_an_unrelated_role_change_leaves_permissions_alone(self):
        other_role = Role.objects.create(name='Unrelated Role', code='UNRELATED')
        reset_education_permissions_on_role_change([other_role.pk])
        self.assertEqual(EducationMemberPermission.objects.filter(committee=self.committee).count(), 1)
