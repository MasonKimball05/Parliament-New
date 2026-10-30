"""
v4.2.0 (10-29-26 auto-run): admin-v2 items from the 10-28 review.

- The service-hours export ran get_user_service_stats() per member (~7
  queries each). It now aggregates once; the numbers must match the
  per-member function exactly, and the query count must not grow with the
  roster.
- ?period= / ?role= that aren't numbers were 500s.
- ?status= went into the CSV's filename unchecked.
- Bulk edit's "nothing selected" redirect copied the whole POST, including
  csrfmiddlewaretoken, into a GET URL.

    python manage.py test src.tests.admin_v2.test_v4_2_0_hardening
"""
import csv
import io
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.models import ServicePeriod
from src.models.service import ServiceHoursAdjustment, ServiceHoursSubmission, ServiceMemberExpectation
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view.service_user_dashboard import get_user_service_stats


def rows(response):
    return list(csv.reader(io.StringIO(response.content.decode())))


class ServiceExportTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.period = ServicePeriod.objects.create(name='Fall', start_date=date(2026, 8, 1),
                                                   end_date=date(2026, 12, 31),
                                                   default_hours_required=Decimal('10'), is_active=True)
        self.members = [make_user(f'svc-{i}') for i in range(3)]
        a, b, c = self.members

        def sub(user, hours, status):
            ServiceHoursSubmission.objects.create(period=self.period, submitted_by=user, hours=Decimal(hours),
                                                  service_date=date(2026, 9, 1), organization='Org',
                                                  description='d', status=status)
        sub(a, '4', 'approved'); sub(a, '2.5', 'approved'); sub(a, '3', 'pending'); sub(a, '9', 'rejected')
        sub(b, '12', 'approved')
        ServiceHoursAdjustment.objects.create(period=self.period, member=a, hours=Decimal('1.5'), reason='r')
        ServiceHoursAdjustment.objects.create(period=self.period, member=c, hours=Decimal('-1'), reason='r')
        ServiceMemberExpectation.objects.create(period=self.period, member=b, expected_hours=Decimal('0'))
        ServiceMemberExpectation.objects.create(period=self.period, member=c, expected_hours=Decimal('5'))

    def export(self, **params):
        return self.client.get(reverse('admin_v2_export_service'), params)

    def test_matches_get_user_service_stats(self):
        self.period.refresh_from_db()   # Decimal('10.00') as stored, like the view reads it
        data = {r[0]: r for r in rows(self.export(period=self.period.pk))[1:]}
        for m in self.members:
            s = get_user_service_stats(m, self.period)
            expected = [m.user_id, m.get_display_name(), 'Fall'] + [str(s[k]) for k in (
                'expected_hours', 'approved_hours', 'pending_hours', 'remaining_hours', 'progress_percent')]
            self.assertEqual(data[m.user_id], expected)

    def test_query_count_does_not_grow_with_roster(self):
        self.export(period=self.period.pk)   # warm one-time caches (flags, settings)
        with CaptureQueriesContext(connection) as small:
            self.export(period=self.period.pk)
        for i in range(10):
            make_user(f'svc-extra-{i}')
        with CaptureQueriesContext(connection) as big:
            self.export(period=self.period.pk)
        self.assertEqual(len(big), len(small))

    def test_non_numeric_period_falls_back_to_active(self):
        r = self.export(period='abc')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(rows(r)[1][2], 'Fall')

    def test_unknown_status_is_not_put_in_the_filename(self):
        r = self.client.get(reverse('admin_v2_export_members'), {'status': 'x";evil'})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('evil', r['Content-Disposition'])
        self.assertIn('members_all_', r['Content-Disposition'])


class BulkParamTests(AdminV2TestCase):

    def test_non_numeric_role_filter_is_not_a_500(self):
        r = self.client.get(reverse('admin_v2_bulk_members'), {'role': 'abc'})
        self.assertEqual(r.status_code, 200)

    def test_nothing_selected_redirect_keeps_filters_not_the_csrf_token(self):
        r = self.client.post(reverse('admin_v2_bulk_members'),
                             {'bulk_action': 'set_status', 'status': 'Active', 'q': 'smith',
                              'csrfmiddlewaretoken': 'tok123'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('csrfmiddlewaretoken', r['Location'])
        self.assertNotIn('tok123', r['Location'])
        self.assertIn('status=Active', r['Location'])
        self.assertIn('q=smith', r['Location'])
