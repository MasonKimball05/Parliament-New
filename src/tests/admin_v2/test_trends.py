"""v4.1.0 admin-v2 trends: weekly counts with zero-filled weeks."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from src.models import LoginHistory
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view.admin_v2_trends import WEEKS, trend_series, week_starts


class TrendsTests(AdminV2TestCase):

    def test_weeks_start_monday_and_end_this_week(self):
        starts = week_starts()
        self.assertEqual(len(starts), WEEKS)
        self.assertTrue(all(s.weekday() == 0 for s in starts))
        self.assertLessEqual((timezone.localdate() - starts[-1]).days, 6)

    def test_distinct_members_and_failed_counts(self):
        m = make_user('trend-m')
        for _ in range(3):
            LoginHistory.objects.create(user=m, status='success', ip_address='203.0.113.1')
        LoginHistory.objects.create(user=self.admin, status='success', ip_address='203.0.113.1')
        LoginHistory.objects.create(user=m, status='failed', ip_address='203.0.113.1')
        old = LoginHistory.objects.create(user=m, status='success', ip_address='203.0.113.1')
        LoginHistory.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(weeks=30))
        series = {s['id']: s for s in trend_series()['series']}
        self.assertEqual(series['signins']['latest'], 2)
        self.assertEqual(series['signins']['total'], 2)
        self.assertEqual(series['failed']['latest'], 1)
        self.assertEqual(len(series['app']['values']), WEEKS)

    def test_page(self):
        r = self.client.get(reverse('admin_v2_trends'))
        self.assertContains(r, 'data-trend="signins"')
        self.assertContains(r, 'id="trend-data"')
