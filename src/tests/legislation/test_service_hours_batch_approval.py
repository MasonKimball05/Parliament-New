"""
v3.29.23 — Mason: "for submissions submitted for multiple days can you
add a way for the approver to approve all the ones submitted together at
once?"

Every row created from one multi-date submit (see
`test_service_hours_multi_date.py`) shares a `batch_id`, set once at
creation by `submit_service_hours`. This file covers the OFFICER side of
that: `view_service_submissions` (`src/view/service_hours.py`) attaches
`batch_pending_count`/`batch_total_count` to each submission it renders,
and `templates/service_hours/view_submissions.html` uses those to render
a per-row "select all N" control that checks every pending sibling's
checkbox — reusing the EXISTING `bulk_actions_service` endpoint and its
existing Approve/Reject bulk-action dropdown rather than adding a new
view. So the real behavior to test is: (a) the counts the view computes
are correct, (b) the template actually renders the control keyed to the
right batch_id, and (c) posting all of a batch's ids to the pre-existing
bulk endpoint approves/rejects the whole batch in one request — which is
the "approve them all together" the feature promises, verified through
the real endpoint rather than assumed from (a) and (b) alone.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from src.models import ServicePeriod, ServiceHoursSubmission, ServiceActivity

ParliamentUser = get_user_model()


class ServiceHoursBatchApprovalTests(TestCase):
    def setUp(self):
        self.member = ParliamentUser.objects.create_user(
            user_id='svcbatch1', name='Batch Member', username='svcbatch1',
            member_type='Member')
        self.member.set_password('testpass')
        self.member.save()

        self.officer = ParliamentUser.objects.create_user(
            user_id='svcofficer1', name='VPP Officer', username='svcofficer1',
            member_type='Officer', is_admin=True)
        self.officer.set_password('testpass')
        self.officer.save()

        self.member_client = Client()
        self.member_client.force_login(self.member)
        self.officer_client = Client()
        self.officer_client.force_login(self.officer)

        today = timezone.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fixture Period',
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
            default_hours_required=Decimal('10.00'),
            requires_approval=True,
        )
        self.today = today

    def _submit_multi_date(self, dates_hours):
        """dates_hours: list of (date, hours_str) EXCLUDING the primary."""
        primary_date, primary_hours = dates_hours[0]
        extra = dates_hours[1:]
        data = {
            'period': self.period.pk,
            'hours': primary_hours,
            'service_date': primary_date.isoformat(),
            'organization': 'Local Food Bank',
            'description': 'Sorted donations.',
            'extra_dates': [d.isoformat() for d, _ in extra],
            'extra_hours': [h for _, h in extra],
        }
        return self.member_client.post(reverse('submit_service_hours'), data)

    # -- the counts the officer page computes ------------------------------

    def test_batch_pending_count_matches_the_number_of_pending_siblings(self):
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
            (self.today - timedelta(days=2), '1.0'),
        ])
        resp = self.officer_client.get(reverse('view_service_submissions'))
        self.assertEqual(resp.status_code, 200)
        for s in resp.context['submissions']:
            self.assertEqual(s.batch_pending_count, 3)
            self.assertEqual(s.batch_total_count, 3)

    def test_single_date_submission_has_zero_batch_counts(self):
        self._submit_multi_date([(self.today, '2.5')])
        resp = self.officer_client.get(reverse('view_service_submissions'))
        submission = resp.context['submissions'][0]
        self.assertIsNone(submission.batch_id)
        self.assertEqual(submission.batch_pending_count, 0)
        self.assertEqual(submission.batch_total_count, 0)

    def test_batch_pending_count_drops_as_siblings_are_reviewed(self):
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
        ])
        submissions = list(ServiceHoursSubmission.objects.order_by('service_date'))
        # Approve one of the two directly (as if reviewed individually).
        one = submissions[0]
        one.status = 'approved'
        one.reviewed_by = self.officer
        one.reviewed_at = timezone.now()
        one.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

        resp = self.officer_client.get(reverse('view_service_submissions'))
        by_id = {s.id: s for s in resp.context['submissions']}
        # The still-pending row now has no pending sibling left...
        self.assertEqual(by_id[submissions[1].id].batch_pending_count, 1)
        # ...but the informational "part of a multi-date submission"
        # count is unaffected by review status.
        self.assertEqual(by_id[submissions[1].id].batch_total_count, 2)
        self.assertEqual(by_id[one.id].batch_total_count, 2)

    # -- the template actually renders the control -------------------------

    def test_page_renders_select_batch_control_for_a_real_batch(self):
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
        ])
        resp = self.officer_client.get(reverse('view_service_submissions'))
        body = resp.content.decode()
        # The static JS at the bottom of the page always mentions the
        # class name once (its querySelectorAll selector) — a rendered
        # button pushes the count to more than that one occurrence.
        self.assertGreater(body.count('select-batch-btn'), 1)
        self.assertIn('data-batch-id=', body)
        self.assertIn('multi-date (2)', body)

    def test_page_does_not_render_select_batch_control_for_a_single_date(self):
        self._submit_multi_date([(self.today, '2.5')])
        resp = self.officer_client.get(reverse('view_service_submissions'))
        body = resp.content.decode()
        # Only the static JS selector reference — no button was rendered.
        self.assertEqual(body.count('select-batch-btn'), 1)
        self.assertNotIn('multi-date (', body)

    # -- the actual promise: approving/rejecting a whole batch at once ----

    def test_bulk_endpoint_approves_every_row_in_a_batch_together(self):
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
            (self.today - timedelta(days=2), '1.0'),
        ])
        submissions = list(ServiceHoursSubmission.objects.all())
        self.assertEqual(len(submissions), 3)
        batch_id = submissions[0].batch_id
        self.assertTrue(all(s.batch_id == batch_id for s in submissions))

        resp = self.officer_client.post(reverse('bulk_actions_service'), {
            'bulk_action': 'approve',
            'submission_ids': [s.id for s in submissions],
        })
        self.assertRedirects(resp, reverse('view_service_submissions'))

        for s in ServiceHoursSubmission.objects.all():
            self.assertEqual(s.status, 'approved')
            self.assertEqual(s.reviewed_by, self.officer)
        # Each row still gets its own activity entry — per-row audit
        # trail is unaffected by approving them together.
        self.assertEqual(
            ServiceActivity.objects.filter(action='approved').count(), 3
        )

    def test_bulk_endpoint_rejects_every_row_in_a_batch_together(self):
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
        ])
        submissions = list(ServiceHoursSubmission.objects.all())

        resp = self.officer_client.post(reverse('bulk_actions_service'), {
            'bulk_action': 'reject',
            'submission_ids': [s.id for s in submissions],
        })
        self.assertRedirects(resp, reverse('view_service_submissions'))
        for s in ServiceHoursSubmission.objects.all():
            self.assertEqual(s.status, 'rejected')

    def test_approving_only_some_of_a_batch_leaves_the_rest_pending(self):
        """The officer isn't forced to act on a whole batch — the
        control is a convenience, not a constraint."""
        self._submit_multi_date([
            (self.today, '2.5'),
            (self.today - timedelta(days=1), '3.0'),
            (self.today - timedelta(days=2), '1.0'),
        ])
        submissions = list(ServiceHoursSubmission.objects.order_by('service_date'))
        self.officer_client.post(reverse('bulk_actions_service'), {
            'bulk_action': 'approve',
            'submission_ids': [submissions[0].id, submissions[1].id],
        })
        refreshed = {s.id: ServiceHoursSubmission.objects.get(id=s.id) for s in submissions}
        self.assertEqual(refreshed[submissions[0].id].status, 'approved')
        self.assertEqual(refreshed[submissions[1].id].status, 'approved')
        self.assertEqual(refreshed[submissions[2].id].status, 'pending')
