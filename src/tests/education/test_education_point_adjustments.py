"""
Manual point adjustments on the education dashboard — 09-23-26, Mason's
request: "can we adjust the amount of points a person has manually? add,
remove, or otherwise adjust... whether it be current or maximum."

THE DESIGN
----------
`PledgePointAdjustment` is an append-only log (src/models/education.py),
not an editable running total — see that model's docstring for why. Every
row's `current_delta`/`max_delta` is summed into `education_home`'s
existing `ps['points']`/`ps['max_points']` (the same numbers
`test_education_dashboard_points.py` covers), scoped to the committee whose
dashboard is open. Undoing an adjustment deletes the row rather than
editing it.

Run with: python manage.py test src.tests.education.test_education_point_adjustments
"""
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import Committee, EducationMemberPermission, PledgePointAdjustment
from src.tests.education._fixtures import EducationFixtureMixin, make_user


class AdjustPointsFoldIntoTheDashboardTotalsTests(EducationFixtureMixin, TestCase):
    """These mirror test_education_dashboard_points.py's own assertion
    style — adjustments are just one more input into the same numbers."""

    def setUp(self):
        self.build()

    def _get(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        summaries = {ps['pledge'].pk: ps for ps in response.context['pledge_summaries']}
        return summaries[self.pledge.pk]

    def test_a_positive_current_adjustment_adds_to_points_only(self):
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 5)
        self.assertEqual(ps['max_points'], 0)
        self.assertTrue(ps['has_adjustments'])

    def test_a_negative_current_adjustment_can_go_below_zero(self):
        """
        Removing more than a pledge has earned is allowed — a chair
        correcting a mistake needs the number to go where they say, not be
        floored at zero behind their back.
        """
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=-5, created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], -5)

    def test_a_max_adjustment_changes_the_denominator_and_the_percent(self):
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, max_delta=10, created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 5)
        self.assertEqual(ps['max_points'], 10)
        self.assertEqual(ps['points_percent'], 50)

    def test_multiple_adjustments_sum(self):
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, reason='Bonus', created_by=self.chair,
        )
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=-2, max_delta=3, reason='Correction', created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 3)
        self.assertEqual(ps['max_points'], 3)

    def test_adjustments_combine_with_earned_task_and_meeting_points(self):
        from src.models import PledgeTask, PledgeTaskCompletion
        task = PledgeTask.objects.create(title='Do a thing', points=10)
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, max_delta=5, created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 15)
        self.assertEqual(ps['max_points'], 15)

    def test_a_pledge_with_no_adjustments_shows_has_adjustments_false(self):
        ps = self._get()
        self.assertFalse(ps['has_adjustments'])

    def test_an_adjustment_on_a_different_committee_does_not_count_here(self):
        """
        `PledgePointAdjustment.committee` is load-bearing — an adjustment
        recorded by a different education committee's dashboard must not
        silently follow the pledge onto this one's.
        """
        other_committee = Committee.objects.create(
            name='Other Education', code='EDU2', is_active=True, is_education_committee=True,
        )
        PledgePointAdjustment.objects.create(
            committee=other_committee, pledge=self.pledge, current_delta=99, created_by=self.chair,
        )
        ps = self._get()
        self.assertEqual(ps['points'], 0)
        self.assertFalse(ps['has_adjustments'])

    def test_a_reason_containing_a_script_close_tag_does_not_break_the_page(self):
        """
        The reason field is free-text chair input embedded into the page's
        script block for the history panel — `_script_safe_json` exists so
        this cannot terminate the <script> tag early. Same fix as
        src/view/quote_book.py's own `_script_safe_json`.
        """
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=1,
            reason='</script><script>alert(1)</script>', created_by=self.chair,
        )
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        html = response.content.decode()
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script>', html)


class AdjustPointsViewTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()

    def _adjust(self, client=None, **data):
        client = client or self.client
        payload = {'current_delta': '0', 'max_delta': '0', 'reason': ''}
        payload.update(data)
        return client.post(
            reverse('education_adjust_points', args=[self.committee.code, self.pledge.pk]),
            payload,
        )

    def test_chair_can_create_an_adjustment(self):
        response = self._adjust(current_delta='5', max_delta='2', reason='Helped with setup')
        self.assertEqual(response.status_code, 200)
        adj = PledgePointAdjustment.objects.get()
        self.assertEqual(adj.committee, self.committee)
        self.assertEqual(adj.pledge, self.pledge)
        self.assertEqual(adj.current_delta, 5)
        self.assertEqual(adj.max_delta, 2)
        self.assertEqual(adj.reason, 'Helped with setup')
        self.assertEqual(adj.created_by, self.chair)

    def test_all_zero_submission_is_rejected_and_writes_nothing(self):
        response = self._adjust(current_delta='0', max_delta='0', reason='just a note')
        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_negative_deltas_are_accepted(self):
        response = self._adjust(current_delta='-3', max_delta='-1')
        self.assertEqual(response.status_code, 200)
        adj = PledgePointAdjustment.objects.get()
        self.assertEqual(adj.current_delta, -3)
        self.assertEqual(adj.max_delta, -1)

    def test_garbage_input_is_treated_as_zero_not_a_500(self):
        response = self._adjust(current_delta='not-a-number', max_delta='5')
        self.assertEqual(response.status_code, 200)
        adj = PledgePointAdjustment.objects.get()
        self.assertEqual(adj.current_delta, 0)
        self.assertEqual(adj.max_delta, 5)

    def test_reason_is_truncated_to_200_chars_not_rejected(self):
        response = self._adjust(current_delta='1', reason='x' * 500)
        self.assertEqual(response.status_code, 200)
        adj = PledgePointAdjustment.objects.get()
        self.assertEqual(len(adj.reason), 200)

    def test_a_member_with_no_education_access_gets_404_not_403(self):
        """
        Matches `_education_committee_or_404`'s own choice everywhere else
        on this page: a member with no standing here should not be able to
        tell, from the response, that this endpoint exists at all.
        """
        outsider = make_user('9010', 'Outsider')
        client = Client()
        client.force_login(outsider)
        response = self._adjust(client=client, current_delta='5')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_view_only_permission_is_not_enough_gets_403(self):
        viewer = make_user('9011', 'View Only')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=viewer,
            can_view_submissions=True, can_grade_submissions=False, can_manage_tasks=False,
        )
        client = Client()
        client.force_login(viewer)
        response = self._adjust(client=client, current_delta='5')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_grade_only_permission_is_not_enough_gets_403(self):
        """
        Adjusting points is gated on `can_manage_tasks`, the same
        permission that already covers editing a task's own point value —
        not `can_grade_submissions`, which is specifically about marking
        quiz answers and completion status.
        """
        grader = make_user('9012', 'Grader')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=grader,
            can_view_submissions=True, can_grade_submissions=True, can_manage_tasks=False,
        )
        client = Client()
        client.force_login(grader)
        response = self._adjust(client=client, current_delta='5')
        self.assertEqual(response.status_code, 403)

    def test_manage_tasks_permission_without_being_chair_is_enough(self):
        manager = make_user('9013', 'Task Manager')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=manager,
            can_view_submissions=True, can_grade_submissions=False, can_manage_tasks=True,
        )
        client = Client()
        client.force_login(manager)
        response = self._adjust(client=client, current_delta='5')
        self.assertEqual(response.status_code, 200)

    def test_adjusting_a_non_pledge_404s(self):
        response = self.client.post(
            reverse('education_adjust_points', args=[self.committee.code, self.brother.pk]),
            {'current_delta': '5'},
        )
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        response = self.client.get(
            reverse('education_adjust_points', args=[self.committee.code, self.pledge.pk])
        )
        self.assertEqual(response.status_code, 405)


class DeletePointAdjustmentViewTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.adjustment = PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, created_by=self.chair,
        )

    def _delete(self, client=None, pk=None):
        client = client or self.client
        return client.post(
            reverse('education_delete_point_adjustment', args=[self.committee.code, pk or self.adjustment.pk])
        )

    def test_chair_can_delete_an_adjustment(self):
        response = self._delete()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        self.assertFalse(PledgePointAdjustment.objects.filter(pk=self.adjustment.pk).exists())

    def test_a_member_with_no_access_gets_404_and_the_row_survives(self):
        outsider = make_user('9020', 'Outsider')
        client = Client()
        client.force_login(outsider)
        response = self._delete(client=client)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(PledgePointAdjustment.objects.filter(pk=self.adjustment.pk).exists())

    def test_view_only_permission_cannot_delete(self):
        viewer = make_user('9021', 'View Only')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=viewer,
            can_view_submissions=True, can_grade_submissions=False, can_manage_tasks=False,
        )
        client = Client()
        client.force_login(viewer)
        response = self._delete(client=client)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(PledgePointAdjustment.objects.filter(pk=self.adjustment.pk).exists())

    def test_cannot_delete_another_committees_adjustment_by_code(self):
        """
        Scoped to `committee=committee` in the lookup — a chair of a
        *different* education committee cannot reach into this one's
        adjustment log by pk, even though they pass their own committee's
        `code` (which they DO have access to) in the URL.
        """
        other_committee = Committee.objects.create(
            name='Other Education', code='EDU2', is_active=True, is_education_committee=True,
        )
        other_chair = make_user('9022', 'Other Chair', member_type='Officer')
        other_committee.chairs.add(other_chair)
        client = Client()
        client.force_login(other_chair)

        response = client.post(
            reverse('education_delete_point_adjustment', args=['EDU2', self.adjustment.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(PledgePointAdjustment.objects.filter(pk=self.adjustment.pk).exists())

    def test_get_is_not_allowed(self):
        response = self.client.get(
            reverse('education_delete_point_adjustment', args=[self.committee.code, self.adjustment.pk])
        )
        self.assertEqual(response.status_code, 405)


class AdjustPointsButtonVisibilityTests(EducationFixtureMixin, TestCase):
    """The button/modal are chair-gated, matching every other management
    control on this page (see test_education_dashboard_button_wiring.py)."""

    def setUp(self):
        self.build()

    def test_chair_sees_the_adjust_button_and_modal(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('data-action="open-adjust-points"', html)
        self.assertIn('id="adjust-points-modal"', html)

    def test_a_view_only_committee_member_does_not_see_the_button(self):
        viewer = make_user('9030', 'View Only')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=viewer,
            can_view_submissions=True, can_grade_submissions=False, can_manage_tasks=False,
        )
        client = Client()
        client.force_login(viewer)
        html = client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertNotIn('data-action="open-adjust-points"', html)


class AdjustPointsQueryScalingTests(EducationFixtureMixin, TestCase):
    """Mirrors PointsSoFarQueryScalingTests in test_education_dashboard_points.py
    — one query for every pledge's adjustments, not one per pledge."""

    def setUp(self):
        self.build()
        PledgePointAdjustment.objects.create(
            committee=self.committee, pledge=self.pledge, current_delta=5, created_by=self.chair,
        )

    def _query_count(self):
        client = Client()
        client.force_login(self.chair)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        return len(captured.captured_queries)

    def test_more_pledges_and_adjustments_does_not_scale_query_count(self):
        baseline = self._query_count()

        for i in range(10):
            pledge = make_user(f'P-ADJ{i:03d}', f'Extra Pledge {i}', member_type='Pledge')
            for _ in range(2):
                PledgePointAdjustment.objects.create(
                    committee=self.committee, pledge=pledge, current_delta=1, created_by=self.chair,
                )

        scaled = self._query_count()
        self.assertLessEqual(
            scaled, baseline + 5,
            f'query count scaled with data volume ({baseline} -> {scaled}) — '
            f'looks like an N+1 was introduced in the adjustment computation',
        )
