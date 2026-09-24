"""
09-24-26 — a member granted education permissions actually SEES the controls.

The server-side permission checks were correct since v3.32.0; the templates
were not (every control gated on `is_chair`). These render the real pages as a
non-chair with an `EducationMemberPermission` row and assert the controls are
present — and absent for a member without the matching permission.

Also covers the ±POINT_ADJUSTMENT_LIMIT bound on manual point adjustments.

Run with: python manage.py test src.tests.education.test_education_permission_ui
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import EducationMemberPermission, PledgePointAdjustment, PledgeTask
from src.tests.education._fixtures import EducationFixtureMixin
from src.view.committee.education import POINT_ADJUSTMENT_LIMIT


class PermittedMembersSeeTheControlsTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.member_client = Client()
        self.member_client.force_login(self.brother)

    def _grant(self, **perms):
        EducationMemberPermission.objects.create(committee=self.committee, user=self.brother, **perms)

    def _home(self):
        r = self.member_client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(r.status_code, 200)
        return r.content.decode()

    def test_a_task_manager_sees_add_task_add_meeting_and_adjust(self):
        self._grant(can_manage_tasks=True)
        html = self._home()
        self.assertIn('data-modal-open="add-task-modal"', html)
        self.assertIn('data-modal-open="addMeetingModal"', html)
        self.assertIn('data-action="open-adjust-points"', html)

    def test_a_view_only_member_does_not_see_management_controls(self):
        self._grant(can_view_submissions=True)
        html = self._home()
        self.assertNotIn('data-modal-open="add-task-modal"', html)
        self.assertNotIn('data-action="open-adjust-points"', html)

    def test_a_task_manager_sees_delete_on_the_task_edit_page(self):
        self._grant(can_manage_tasks=True)
        task = PledgeTask.objects.create(title='Learn the creed', points=5)
        r = self.member_client.get(reverse('education_edit_task', args=[self.committee.code, task.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('delete-task-trigger', r.content.decode())


class PointAdjustmentBoundsTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()

    def _post(self, **data):
        return self.client.post(
            reverse('education_adjust_points', args=[self.committee.code, self.pledge.pk]), data,
        )

    def test_an_out_of_range_delta_is_a_400_not_a_database_error(self):
        # SQLite does not enforce SmallIntegerField's width; PostgreSQL raises
        # NumericValueOutOfRange. The view must reject before the INSERT.
        for field in ('current_delta', 'max_delta'):
            with self.subTest(field=field):
                r = self._post(**{field: str(POINT_ADJUSTMENT_LIMIT + 1)})
                self.assertEqual(r.status_code, 400)
                r = self._post(**{field: str(-(POINT_ADJUSTMENT_LIMIT + 1))})
                self.assertEqual(r.status_code, 400)
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_the_limit_itself_is_accepted(self):
        r = self._post(current_delta=str(POINT_ADJUSTMENT_LIMIT))
        self.assertEqual(r.status_code, 200)
