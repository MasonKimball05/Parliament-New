"""v4.1.0 admin-v2 exports."""
import csv
import io
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from src.models import Attendance, Event
from src.tests.admin_v2.base import AdminV2TestCase, make_user


def rows(response):
    return list(csv.reader(io.StringIO(response.content.decode())))


class ExportTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.e = make_user('exp-e', name='=HYPERLINK("x")', member_status='Active')
        self.alum = make_user('exp-alum', member_status='Alumni')

    def test_page_lists_downloads(self):
        r = self.client.get(reverse('admin_v2_exports'))
        self.assertContains(r, reverse('admin_v2_export_members'))
        self.assertContains(r, reverse('export_directory'))

    def test_roster_filters_by_status_and_is_formula_safe(self):
        r = self.client.get(reverse('admin_v2_export_members'), {'status': 'Active'})
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        data = rows(r)
        numbers = [row[0] for row in data[1:]]
        self.assertIn('exp-e', numbers)
        self.assertNotIn('exp-alum', numbers)
        name = next(row[1] for row in data if row[0] == 'exp-e')
        self.assertTrue(name.startswith("'="))

    def test_attendance_rates(self):
        for i, status in enumerate(('present', 'absent')):
            ev = Event.objects.create(title=f'E{i}', date_time=timezone.now() - timedelta(days=i + 1),
                                      created_by=self.admin, requires_attendance=True)
            Attendance.objects.create(event=ev, user=self.e, status=status, attendance_type='event')
        data = rows(self.client.get(reverse('admin_v2_export_attendance'), {'days': 30}))
        row = next(r for r in data if r[0] == 'exp-e')
        self.assertEqual(row[4:], ['2', '1', '0', '1', '0', '50'])

    def test_service_hours_without_a_period(self):
        data = rows(self.client.get(reverse('admin_v2_export_service')))
        self.assertEqual(len(data), 1)  # header only
