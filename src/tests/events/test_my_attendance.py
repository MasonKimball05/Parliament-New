"""
`/my-attendance/` reports the right numbers.

⚠️ WHY THIS FILE IS NOT ALSO THE BUDGET TESTS ANY MORE. v3.24.0 rewrote this
page to stop costing two queries per event, and shipped the scaling assertions
here alongside the arithmetic ones. v3.25.0 moved the scaling half into
`src/test_query_budgets.py` as `MyAttendanceQueryBudgetTests`, with a measured
`BUDGET`, because that is where this project's ceilings live and where the
ratchet that keeps them honest can see them.

**A query-count test cannot see a wrong answer.** That is v3.18.6's lesson — a
missing `.order_by()` on a grouped aggregate produces a *fast* page with one row
per submission — and it is why these tests exist separately from the budget at
all. The rewrite moved five `COUNT(*)`s and two per-event lookups out of the
database and into Python dictionaries; every assertion below pins arithmetic or
scoping that a query count would happily let drift.

The page's history and cost are recorded on the budget class. The short version:
it was linked from nowhere until v3.22.0 and cost 349 queries at 120 events.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Attendance, AttendanceExcuse, Event, ParliamentUser


class MyAttendanceStillReportsTheSameNumbersTests(TestCase):
    """
    ⚠️ A QUERY-COUNT TEST CANNOT SEE A WRONG ANSWER — v3.18.6's lesson, where
    a missing `.order_by()` on a grouped aggregate would have produced a fast
    page with one row per submission. These pin the arithmetic the rewrite moved
    out of the database and into Python.
    """

    PASSWORD = 'my-attendance-numbers-pass-12345!'

    def setUp(self):
        self.officer = ParliamentUser.objects.create_user(
            user_id='MA-OFF2', password=self.PASSWORD, name='Officer',
            username='ma_officer2', member_type='Officer', is_admin=True,
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='P-STATS1', password=self.PASSWORD, name='Member',
            username='ma_member2', member_type='Member',
        )
        self.other = ParliamentUser.objects.create_user(
            user_id='P-STATS2', password=self.PASSWORD, name='Other',
            username='ma_member3', member_type='Member',
        )

    def _event(self, days_ago, title='Meeting'):
        return Event.objects.create(
            title=title, description='x',
            date_time=timezone.now() - timedelta(days=days_ago),
            created_by=self.officer, requires_attendance=True, is_active=True,
        )

    def test_personal_counts_and_rate(self):
        statuses = ['present', 'present', 'late', 'absent', 'excused']
        for i, status in enumerate(statuses):
            Attendance.objects.create(
                user=self.member, event=self._event(i + 1), status=status,
                attendance_type='event',
            )

        self.client.force_login(self.member)
        stats = self.client.get(reverse('my_attendance')).context['my_stats']

        self.assertEqual(stats['total'], 5)
        self.assertEqual(stats['present'], 2)
        self.assertEqual(stats['late'], 1)
        self.assertEqual(stats['absent'], 1)
        self.assertEqual(stats['excused'], 1)
        # present + late over total: 3/5.
        self.assertEqual(stats['attendance_rate'], 60.0)

    def test_the_chapter_average_counts_the_whole_chapter(self):
        """
        Regression guard for the aggregate that replaced two counts: the
        member's own rate and the chapter's must be able to differ.
        """
        event = self._event(1)
        Attendance.objects.create(user=self.member, event=event,
                                  status='present', attendance_type='event')
        Attendance.objects.create(user=self.other, event=event,
                                  status='absent', attendance_type='event')

        self.client.force_login(self.member)
        context = self.client.get(reverse('my_attendance')).context

        self.assertEqual(context['my_stats']['attendance_rate'], 100.0)
        self.assertEqual(context['chapter_average'], 50.0)
        self.assertEqual(context['rate_difference'], 50.0)

    def test_the_history_row_carries_its_excuse_and_status(self):
        """The two dictionaries that replaced the per-event queries."""
        attended = self._event(1, title='Attended')
        excused = self._event(2, title='Excused')
        unmarked = self._event(3, title='Unmarked')

        Attendance.objects.create(user=self.member, event=attended,
                                  status='present', attendance_type='event')
        AttendanceExcuse.objects.create(user=self.member, event=excused,
                                        reason='away')

        self.client.force_login(self.member)
        history = self.client.get(reverse('my_attendance')).context[
            'attendance_history']
        by_title = {row['event'].title: row for row in history}

        self.assertEqual(by_title['Attended']['status'], 'present')
        self.assertIsNone(by_title['Attended']['excuse'])
        self.assertEqual(by_title['Excused']['status'], 'not_marked')
        self.assertIsNotNone(by_title['Excused']['excuse'])
        self.assertEqual(by_title['Unmarked']['status'], 'not_marked')
        self.assertIsNone(by_title['Unmarked']['excuse'])

    def test_another_members_excuse_is_not_shown_on_this_page(self):
        """
        ⚠️ The excuse lookup went from `.filter(event=…, user=request.user)` to
        a dict built from one query. If the `user=` half were ever dropped while
        batching, every row would silently carry somebody else's excuse — and
        the query count would look *better*, not worse.
        """
        event = self._event(1, title='Shared')
        AttendanceExcuse.objects.create(user=self.other, event=event,
                                        reason='not yours')

        self.client.force_login(self.member)
        history = self.client.get(reverse('my_attendance')).context[
            'attendance_history']

        self.assertIsNone(history[0]['excuse'])

    def test_series_stats_group_by_parent(self):
        parent = Event.objects.create(
            title='Weekly Chapter', description='x',
            date_time=timezone.now() - timedelta(days=21),
            created_by=self.officer, requires_attendance=True, is_active=True,
            is_recurring=True,
        )
        for i, status in enumerate(['present', 'present', 'absent']):
            child = Event.objects.create(
                title='Weekly Chapter', description='x',
                date_time=timezone.now() - timedelta(days=14 - i * 7),
                created_by=self.officer, requires_attendance=True,
                is_active=True, parent_event=parent,
            )
            Attendance.objects.create(user=self.member, event=child,
                                      status=status, attendance_type='event')

        self.client.force_login(self.member)
        series = self.client.get(reverse('my_attendance')).context[
            'series_stats']

        self.assertEqual(len(series), 1)
        self.assertEqual(series[0]['title'], 'Weekly Chapter')
        self.assertEqual(series[0]['attended_count'], 2)
        self.assertEqual(series[0]['attendance_rate'], round(2 / 3 * 100, 1))
