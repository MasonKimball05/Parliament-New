"""
Kai dashboard statistics — regression tests (v3.16.3, 07-28-26).

The six-month trend chart walked months with
`timezone.now() - timedelta(days=30 * i)`, which is not one step per calendar
month. On 32 days of 2026 two steps landed in the same month, so one dict key
overwrote another and the chart silently rendered five bars instead of six. On
2026-03-01 the keys came out ['Oct','Nov','Dec','Dec','Jan','Mar'] — February
missing entirely, December double-counted. No error, no log line.

The same block also fired one COUNT per month, plus four for status and three
for outcomes, on every page load.
"""

import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import ParliamentUser, Committee, KaiReport, KaiMemberPermission


def _months_back(reference, n):
    """First instant of the calendar month `n` months before `reference`."""
    cursor = reference.replace(day=1, hour=12, minute=0, second=0, microsecond=0)
    for _ in range(n):
        cursor = (cursor - timedelta(days=1)).replace(day=1, hour=12)
    return cursor


class MonthlyTrendTests(TestCase):
    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='dsub', name='Dash Submitter', username='dsub', member_type='Member')
        self.chair = ParliamentUser.objects.create_user(
            user_id='dchair', name='Dana Chair', username='dchair', member_type='Officer')

        self.committee = Committee.objects.create(
            name='Kai Committee (dash)', code='KAIDASH', is_kai_committee=True)
        self.committee.chairs.add(self.chair)
        self.client.force_login(self.chair)

    def _monthly_data(self):
        resp = self.client.get(reverse('view_kai_reports'))
        self.assertEqual(resp.status_code, 200)
        return json.loads(resp.context['monthly_data'])

    def test_chart_always_has_six_distinct_months(self):
        """The bug: two 30-day steps landing in one calendar month collapsed a key."""
        data = self._monthly_data()
        self.assertEqual(len(data), 6, 'expected 6 buckets, got %r' % list(data))

    def test_months_are_consecutive_and_end_with_the_current_month(self):
        now = timezone.localtime()
        expected = [
            _months_back(now, n).strftime('%b %Y') for n in range(5, -1, -1)
        ]
        self.assertEqual(list(self._monthly_data().keys()), expected)

    def test_reports_land_in_their_own_month(self):
        now = timezone.localtime()
        # One report in the current month, two in the month before it.
        KaiReport.objects.create(
            title='this month', category='behavioral', description='d',
            submitted_by=self.submitter)
        for i in range(2):
            report = KaiReport.objects.create(
                title='last month %d' % i, category='behavioral', description='d',
                submitted_by=self.submitter)
            # submitted_at is auto_now_add; move it explicitly.
            KaiReport.objects.filter(pk=report.pk).update(
                submitted_at=_months_back(now, 1))

        data = self._monthly_data()
        self.assertEqual(data[_months_back(now, 0).strftime('%b %Y')], 1)
        self.assertEqual(data[_months_back(now, 1).strftime('%b %Y')], 2)

    def test_months_with_no_reports_are_zero_not_missing(self):
        data = self._monthly_data()
        self.assertTrue(all(v == 0 for v in data.values()))
        self.assertEqual(len(data), 6)

    def test_reports_older_than_the_window_are_excluded(self):
        now = timezone.localtime()
        report = KaiReport.objects.create(
            title='ancient', category='behavioral', description='d',
            submitted_by=self.submitter)
        KaiReport.objects.filter(pk=report.pk).update(submitted_at=_months_back(now, 9))

        data = self._monthly_data()
        self.assertEqual(sum(data.values()), 0)


class DashboardCountTests(TestCase):
    def setUp(self):
        self.submitter = ParliamentUser.objects.create_user(
            user_id='csub', name='Count Submitter', username='csub', member_type='Member')
        self.chair = ParliamentUser.objects.create_user(
            user_id='cchair', name='Cora Chair', username='cchair', member_type='Officer')
        self.committee = Committee.objects.create(
            name='Kai Committee (counts)', code='KAICNT', is_kai_committee=True)
        self.committee.chairs.add(self.chair)

        for status in ('pending', 'pending', 'reviewed', 'archived'):
            KaiReport.objects.create(
                title='c-%s' % status, category='behavioral', description='d',
                submitted_by=self.submitter, status=status)

        self.client.force_login(self.chair)

    def test_status_counts_are_correct_after_the_aggregate_rewrite(self):
        """Four .count() calls became one values().annotate() — same numbers."""
        resp = self.client.get(reverse('view_kai_reports'))
        counts = resp.context['counts']
        self.assertEqual(counts['all'], 4)
        self.assertEqual(counts['pending'], 2)
        self.assertEqual(counts['reviewed'], 1)
        self.assertEqual(counts['archived'], 1)

    def test_outcome_counts_are_correct_after_the_aggregate_rewrite(self):
        resp = self.client.get(reverse('view_kai_reports'))
        # All four seeded reports default to deliberation_outcome='pending'.
        self.assertEqual(resp.context['outcome_pending'], 4)
        self.assertEqual(resp.context['outcome_heard'], 0)
        self.assertEqual(resp.context['outcome_thrown_out'], 0)

    def test_stats_do_not_regress_into_per_month_counting(self):
        """
        Budget check. Before v3.16.3 the stats block alone was 13 queries
        (4 status + 3 outcome + 6 monthly); it is now 3. The ceiling is
        deliberately loose — it exists to catch a return to per-month counting,
        not to pin an exact number.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('view_kai_reports'))

        monthly_like = [
            q for q in ctx.captured_queries
            if 'submitted_at' in q['sql'] and 'COUNT' in q['sql'].upper()
        ]
        self.assertLessEqual(
            len(monthly_like), 2,
            'the six-month trend looks like it is counting per month again: %d queries'
            % len(monthly_like),
        )
