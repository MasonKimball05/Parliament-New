"""
v3.29.22 — Mason: "Can you update the form for service hours to allow for
selecting multiple dates as well? Currently you can only select 1 date."

`submit_service_hours` (`src/view/service_user_dashboard.py`) now accepts
extra dates via `request.POST.getlist('extra_dates')` — one plain
`<input type="date" name="extra_dates">` per row added by the template's
"+ Add another date" button — and creates one `ServiceHoursSubmission`
per date (same hours/organization/description/attachment/status on
every row), rather than one row somehow spanning several dates. See the
view's own docstring for why: approval, editing, and the CSV export are
all per-row features this model already has, and a multi-date ROW would
have to redefine all three; multiple rows sharing everything but the date
needs none of them touched.

These tests exercise the real view end-to-end via the Django test client
— not the helper functions in isolation — so a regression in how the
pieces fit together (e.g. the custom-field-cloning avoiding a second read
of `request.FILES`) would actually be caught.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ServicePeriod, ServiceHoursSubmission, ServiceActivity,
    ServiceFormField, ServiceFieldResponse, Role,
)

ParliamentUser = get_user_model()


class ServiceHoursMultiDateSubmissionTests(TestCase):
    def setUp(self):
        self.member = ParliamentUser.objects.create_user(
            user_id='svcmulti1', name='Service Member', username='svcmulti1',
            member_type='Member')
        self.member.set_password('testpass')
        self.member.save()
        self.client = Client()
        self.client.force_login(self.member)

        today = timezone.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fixture Period',
            start_date=today - timedelta(days=30),
            end_date=today + timedelta(days=30),
            default_hours_required=Decimal('10.00'),
            requires_approval=True,
        )
        self.today = today

    def _base_post_data(self, **overrides):
        data = {
            'period': self.period.pk,
            'hours': '2.5',
            'service_date': self.today.isoformat(),
            'organization': 'Local Food Bank',
            'description': 'Sorted donations.',
        }
        data.update(overrides)
        return data

    # -- backward compatibility: single date, no extra_dates at all -------

    def test_single_date_still_creates_exactly_one_submission(self):
        resp = self.client.post(reverse('submit_service_hours'), self._base_post_data())
        self.assertRedirects(resp, reverse('user_service_dashboard'))
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)
        submission = ServiceHoursSubmission.objects.get()
        self.assertEqual(submission.service_date, self.today)
        self.assertEqual(submission.hours, Decimal('2.5'))
        self.assertEqual(ServiceActivity.objects.filter(submission=submission).count(), 1)

    # -- the actual feature: multiple dates --------------------------------

    def test_extra_dates_create_one_row_each_with_shared_fields(self):
        extra1 = self.today - timedelta(days=7)
        extra2 = self.today - timedelta(days=14)
        data = self._base_post_data()
        resp = self.client.post(
            reverse('submit_service_hours'),
            {**data, 'extra_dates': [extra1.isoformat(), extra2.isoformat()]},
        )
        self.assertRedirects(resp, reverse('user_service_dashboard'))

        submissions = ServiceHoursSubmission.objects.order_by('service_date')
        self.assertEqual(submissions.count(), 3)
        dates = [s.service_date for s in submissions]
        self.assertEqual(dates, sorted([self.today, extra1, extra2]))
        for s in submissions:
            self.assertEqual(s.hours, Decimal('2.5'))
            self.assertEqual(s.organization, 'Local Food Bank')
            self.assertEqual(s.description, 'Sorted donations.')
            self.assertEqual(s.submitted_by, self.member)
            self.assertEqual(s.period, self.period)
            self.assertEqual(s.status, 'pending')  # period.requires_approval=True

    def test_each_date_gets_its_own_activity_log_entry(self):
        extra = self.today - timedelta(days=3)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()]},
        )
        self.assertEqual(ServiceActivity.objects.count(), 2)
        for activity in ServiceActivity.objects.all():
            self.assertEqual(activity.action, 'created')
            self.assertIn('multi-date submission, 2 dates', activity.details)

    def test_auto_approved_period_marks_every_date_approved(self):
        self.period.requires_approval = False
        self.period.save()
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()]},
        )
        submissions = ServiceHoursSubmission.objects.all()
        self.assertEqual(submissions.count(), 2)
        for s in submissions:
            self.assertEqual(s.status, 'approved')
            self.assertIsNotNone(s.reviewed_at)

    # -- input hygiene: blanks, duplicates, malformed values ---------------

    def test_blank_extra_date_rows_are_ignored(self):
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': ['', '  ']},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_date_equal_to_primary_date_is_not_duplicated(self):
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [self.today.isoformat()]},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_repeated_extra_dates_are_deduplicated(self):
        extra = self.today - timedelta(days=5)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat(), extra.isoformat()]},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 2)

    def test_malformed_extra_date_is_silently_dropped_not_a_500(self):
        resp = self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': ['not-a-date', '2026-13-40']},
        )
        self.assertRedirects(resp, reverse('user_service_dashboard'))
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_dates_beyond_the_cap_are_truncated(self):
        from src.view.service_user_dashboard import MAX_EXTRA_SERVICE_DATES

        many_dates = [
            (self.today - timedelta(days=100 + i)).isoformat()
            for i in range(MAX_EXTRA_SERVICE_DATES + 10)
        ]
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': many_dates},
        )
        # +1 for the primary date itself.
        self.assertEqual(ServiceHoursSubmission.objects.count(), MAX_EXTRA_SERVICE_DATES + 1)

    # -- attachment sharing --------------------------------------------------

    def test_attachment_is_shared_across_dates_not_reuploaded_separately(self):
        extra = self.today - timedelta(days=2)
        upload = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf')
        resp = self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'attachment': upload},
        )
        self.assertRedirects(resp, reverse('user_service_dashboard'))
        submissions = list(ServiceHoursSubmission.objects.order_by('service_date'))
        self.assertEqual(len(submissions), 2)
        self.assertTrue(all(s.attachment for s in submissions))
        # Same underlying stored file, not two separate uploads.
        self.assertEqual(submissions[0].attachment.name, submissions[1].attachment.name)

    def test_no_attachment_means_no_attachment_on_any_clone(self):
        extra = self.today - timedelta(days=2)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()]},
        )
        for s in ServiceHoursSubmission.objects.all():
            self.assertFalse(s.attachment)

    # -- custom fields --------------------------------------------------------

    def test_custom_text_field_value_is_copied_to_every_date(self):
        field = ServiceFormField.objects.create(
            field_name='supervisor_name', label='Supervisor Name',
            field_type='text', is_active=True, is_builtin=False,
        )
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra.isoformat()],
                'custom_supervisor_name': 'Jane Doe',
            },
        )
        submissions = ServiceHoursSubmission.objects.all()
        self.assertEqual(submissions.count(), 2)
        responses = ServiceFieldResponse.objects.filter(field=field)
        self.assertEqual(responses.count(), 2)
        for r in responses:
            self.assertEqual(r.text_value, 'Jane Doe')
        # Each response belongs to a different submission row.
        self.assertEqual(
            set(responses.values_list('submission_id', flat=True)),
            set(submissions.values_list('id', flat=True)),
        )

    # -- notification batching -----------------------------------------------

    def test_multi_date_submission_sends_exactly_one_notification(self):
        vpp_role, _ = Role.objects.get_or_create(code='VPP', defaults={'name': 'VP of Programming'})
        vpp = ParliamentUser.objects.create_user(
            user_id='svcvpp1', name='VPP Officer', username='svcvpp1',
            member_type='Officer', email='vpp@example.com')
        vpp.roles.add(vpp_role)

        extra = self.today - timedelta(days=1)
        with patch('src.view.service_user_dashboard.send_email') as mock_send:
            self.client.post(
                reverse('submit_service_hours'),
                {**self._base_post_data(), 'extra_dates': [extra.isoformat()]},
            )
        self.assertEqual(mock_send.delay.call_count, 1)
        (subject, message, _from, recipients), _ = mock_send.delay.call_args
        self.assertIn('2 dates', subject)
        self.assertEqual(recipients, ['vpp@example.com'])

    def test_single_date_notification_unchanged_shape(self):
        """Backward-compat: a single-date submission still goes through
        the original single-submission notify path, not the batch one."""
        vpp_role, _ = Role.objects.get_or_create(code='VPP', defaults={'name': 'VP of Programming'})
        vpp = ParliamentUser.objects.create_user(
            user_id='svcvpp2', name='VPP Officer Two', username='svcvpp2',
            member_type='Officer', email='vpp2@example.com')
        vpp.roles.add(vpp_role)

        with patch('src.view.service_user_dashboard.send_email') as mock_send:
            self.client.post(reverse('submit_service_hours'), self._base_post_data())
        self.assertEqual(mock_send.delay.call_count, 1)
        (subject, message, _from, recipients), _ = mock_send.delay.call_args
        self.assertNotIn('dates', subject)
