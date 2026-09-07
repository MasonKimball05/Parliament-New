"""
v3.29.22 — Mason: "Can you update the form for service hours to allow for
selecting multiple dates as well? Currently you can only select 1 date."

v3.29.23 — Mason: "When selecting each day instead of assuming that it is
x hours exactly each day can it instead require the person to enter the
number of hours they put in for those days?" Each "+ Add another date"
row now posts a PAIRED `extra_dates`/`extra_hours` value (index-aligned —
see `submit_hours.html`'s `addDateRow()`), and every clone gets its own
validated hours instead of copying the primary submission's. This file
was rewritten in v3.29.23 to post `extra_hours` alongside every
`extra_dates` value; without it, `_extra_service_entries_from_post` zips
an empty hours list against the dates and produces nothing, which is
itself the reason the pre-v3.29.23 tests all failed once posted without
the new field — see the v3.29.23 changelog's negative control.

`submit_service_hours` (`src/view/service_user_dashboard.py`) creates one
`ServiceHoursSubmission` per date (same organization/description/
attachment/status on every row, but each date's OWN hours), rather than
one row somehow spanning several dates. See the view's own docstring for
why: approval, editing, and the CSV export are all per-row features this
model already has, and a multi-date ROW would have to redefine all
three; multiple rows sharing everything but date/hours needs none of them
touched.

v3.29.23 also added `batch_id` — set once, at creation, on every row that
came out of the same multi-date POST — so the officer review page can
offer "approve/reject all N dates from this submission together"
(`view_service_submissions` / `bulk_actions_service`, tested separately
in `test_service_hours_batch_approval.py`).

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
        self.assertIsNone(submission.batch_id)
        self.assertEqual(ServiceActivity.objects.filter(submission=submission).count(), 1)

    # -- the actual feature: multiple dates, each with its own hours ------

    def test_extra_dates_create_one_row_each_with_own_hours(self):
        extra1 = self.today - timedelta(days=7)
        extra2 = self.today - timedelta(days=14)
        data = self._base_post_data()
        resp = self.client.post(
            reverse('submit_service_hours'),
            {
                **data,
                'extra_dates': [extra1.isoformat(), extra2.isoformat()],
                'extra_hours': ['1.5', '3.0'],
            },
        )
        self.assertRedirects(resp, reverse('user_service_dashboard'))

        submissions = ServiceHoursSubmission.objects.order_by('service_date')
        self.assertEqual(submissions.count(), 3)
        by_date = {s.service_date: s for s in submissions}
        self.assertEqual(by_date[self.today].hours, Decimal('2.5'))
        self.assertEqual(by_date[extra1].hours, Decimal('1.5'))
        self.assertEqual(by_date[extra2].hours, Decimal('3.0'))
        for s in submissions:
            self.assertEqual(s.organization, 'Local Food Bank')
            self.assertEqual(s.description, 'Sorted donations.')
            self.assertEqual(s.submitted_by, self.member)
            self.assertEqual(s.period, self.period)
            self.assertEqual(s.status, 'pending')  # period.requires_approval=True

    def test_each_date_gets_its_own_activity_log_entry_with_its_own_hours(self):
        extra = self.today - timedelta(days=3)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['4.0']},
        )
        self.assertEqual(ServiceActivity.objects.count(), 2)
        primary = ServiceHoursSubmission.objects.get(service_date=self.today)
        clone = ServiceHoursSubmission.objects.get(service_date=extra)
        primary_activity = ServiceActivity.objects.get(submission=primary)
        clone_activity = ServiceActivity.objects.get(submission=clone)
        self.assertIn('Submitted 2.5 hours', primary_activity.details)
        self.assertIn('Submitted 4.0 hours', clone_activity.details)
        self.assertIn('multi-date submission, 2 dates', primary_activity.details)

    def test_auto_approved_period_marks_every_date_approved(self):
        self.period.requires_approval = False
        self.period.save()
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['1.0']},
        )
        submissions = ServiceHoursSubmission.objects.all()
        self.assertEqual(submissions.count(), 2)
        for s in submissions:
            self.assertEqual(s.status, 'approved')
            self.assertIsNotNone(s.reviewed_at)

    # -- batch_id: what ties the rows together for bulk approval ----------

    def test_multi_date_rows_share_a_batch_id(self):
        extra1 = self.today - timedelta(days=7)
        extra2 = self.today - timedelta(days=14)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra1.isoformat(), extra2.isoformat()],
                'extra_hours': ['1.5', '3.0'],
            },
        )
        submissions = list(ServiceHoursSubmission.objects.all())
        self.assertEqual(len(submissions), 3)
        batch_ids = {s.batch_id for s in submissions}
        self.assertEqual(len(batch_ids), 1)
        self.assertIsNotNone(next(iter(batch_ids)))

    def test_single_date_submission_has_no_batch_id(self):
        self.client.post(reverse('submit_service_hours'), self._base_post_data())
        submission = ServiceHoursSubmission.objects.get()
        self.assertIsNone(submission.batch_id)

    def test_two_separate_submissions_get_two_different_batch_ids(self):
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['1.0']},
        )
        first_batch = set(ServiceHoursSubmission.objects.values_list('batch_id', flat=True))

        extra2 = self.today - timedelta(days=20)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(service_date=(self.today - timedelta(days=19)).isoformat()),
                'extra_dates': [extra2.isoformat()],
                'extra_hours': ['2.0'],
            },
        )
        second_batch = set(ServiceHoursSubmission.objects.exclude(
            batch_id__in=first_batch
        ).values_list('batch_id', flat=True))
        self.assertTrue(first_batch)
        self.assertTrue(second_batch)
        self.assertEqual(first_batch & second_batch, set())

    # -- input hygiene: blanks, duplicates, malformed dates ----------------

    def test_blank_extra_date_rows_are_ignored(self):
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': ['', '  '], 'extra_hours': ['', '']},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_date_equal_to_primary_date_is_not_duplicated(self):
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [self.today.isoformat()], 'extra_hours': ['1.0']},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_repeated_extra_dates_are_deduplicated(self):
        extra = self.today - timedelta(days=5)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra.isoformat(), extra.isoformat()],
                'extra_hours': ['1.0', '9.0'],
            },
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 2)

    def test_malformed_extra_date_is_silently_dropped_not_a_500(self):
        resp = self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': ['not-a-date', '2026-13-40'],
                'extra_hours': ['1.0', '1.0'],
            },
        )
        self.assertRedirects(resp, reverse('user_service_dashboard'))
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_dates_beyond_the_cap_are_truncated(self):
        from src.view.service_user_dashboard import MAX_EXTRA_SERVICE_DATES

        many_dates = [
            (self.today - timedelta(days=100 + i)).isoformat()
            for i in range(MAX_EXTRA_SERVICE_DATES + 10)
        ]
        many_hours = ['1.0'] * len(many_dates)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': many_dates, 'extra_hours': many_hours},
        )
        # +1 for the primary date itself.
        self.assertEqual(ServiceHoursSubmission.objects.count(), MAX_EXTRA_SERVICE_DATES + 1)

    # -- input hygiene: the new part in v3.29.23 — invalid HOURS ----------

    def test_extra_hours_blank_drops_that_date_with_a_warning(self):
        extra = self.today - timedelta(days=1)
        resp = self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['']},
            follow=True,
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)
        warnings = [m for m in resp.context['messages']] if resp.context else []
        self.assertTrue(any('skipped' in str(m) for m in warnings))

    def test_extra_hours_non_numeric_drops_that_date(self):
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['not-a-number']},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_hours_zero_or_negative_drops_that_date(self):
        extra1 = self.today - timedelta(days=1)
        extra2 = self.today - timedelta(days=2)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra1.isoformat(), extra2.isoformat()],
                'extra_hours': ['0', '-3'],
            },
        )
        # Only the primary submission should have been created.
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_extra_hours_over_24_drops_that_date(self):
        extra = self.today - timedelta(days=1)
        self.client.post(
            reverse('submit_service_hours'),
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['25']},
        )
        self.assertEqual(ServiceHoursSubmission.objects.count(), 1)

    def test_one_bad_hours_value_does_not_block_the_other_valid_dates(self):
        extra_good = self.today - timedelta(days=1)
        extra_bad = self.today - timedelta(days=2)
        self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra_good.isoformat(), extra_bad.isoformat()],
                'extra_hours': ['3.25', 'garbage'],
            },
        )
        submissions = ServiceHoursSubmission.objects.order_by('service_date')
        self.assertEqual(submissions.count(), 2)
        self.assertEqual(
            ServiceHoursSubmission.objects.get(service_date=extra_good).hours,
            Decimal('3.25'),
        )
        self.assertFalse(ServiceHoursSubmission.objects.filter(service_date=extra_bad).exists())

    # -- attachment sharing --------------------------------------------------

    def test_attachment_is_shared_across_dates_not_reuploaded_separately(self):
        extra = self.today - timedelta(days=2)
        upload = SimpleUploadedFile('receipt.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf')
        resp = self.client.post(
            reverse('submit_service_hours'),
            {
                **self._base_post_data(),
                'extra_dates': [extra.isoformat()],
                'extra_hours': ['1.0'],
                'attachment': upload,
            },
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
            {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['1.0']},
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
                'extra_hours': ['1.0'],
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
                {**self._base_post_data(), 'extra_dates': [extra.isoformat()], 'extra_hours': ['4.0']},
            )
        self.assertEqual(mock_send.delay.call_count, 1)
        (subject, message, _from, recipients), _ = mock_send.delay.call_args
        self.assertIn('2 dates', subject)
        self.assertIn('6.5 hrs', subject)  # 2.5 + 4.0 total, not "hrs each"
        self.assertIn('hrs)', message)  # per-date hours shown in the body
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
