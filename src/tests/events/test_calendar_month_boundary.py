"""
Calendar month-boundary bug — an event on the last day of a month, late in
the evening (chapter local time), could vanish from the calendar entirely.

Root cause: calendar_view and calendar_data_api computed their query window
(month_start/month_end) by localizing naive month boundaries as UTC, while
grouping events for display by LOCAL calendar day
(timezone.localtime(event.date_time).day). Event rows are stored in UTC
(USE_TZ=True), so a late-evening local event near month-end could be stored
as just past midnight UTC on the 1st of the NEXT month — excluded from the
current month's query, pulled into next month's instead, and then grouped
there under a day-of-month number (e.g. 31) that a shorter next month
doesn't have a calendar cell for. The event disappeared from both months.

Reported symptom: an event placed on October 31 didn't show; the 30th
worked. This reproduces that exact shape with an 11:55 PM local event on the
last day of a 31-day month rolling into a 30-day next month, and proves the
fix (localizing the query window in the chapter's real timezone, matching
the display grouping) makes it show up in the correct month and nowhere
else — regardless of whether that particular date happens to be in
daylight or standard time, since 23:55 local plus either the CDT (-5) or
CST (-6) offset both roll past midnight UTC.
"""
import json
from datetime import datetime

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Event, ParliamentUser


def make_member(uid='cal-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Cal Member', username=uid, member_type='Member',
        member_status='Active',
    )


class CalendarMonthBoundaryTests(TestCase):
    """October (31 days) rolling into November (30 days) — the reported case."""

    def setUp(self):
        self.user = make_member()
        self.client = Client()
        self.client.force_login(self.user)
        self.late_event = Event.objects.create(
            title='Late Halloween Meeting',
            description='D',
            date_time=timezone.make_aware(datetime(2026, 10, 31, 23, 55)),
            is_active=True,
            created_by=self.user,
        )
        self.control_event = Event.objects.create(
            title='30th Meeting',
            description='D',
            date_time=timezone.make_aware(datetime(2026, 10, 30, 23, 55)),
            is_active=True,
            created_by=self.user,
        )

    def test_late_event_appears_in_its_local_month_view(self):
        resp = self.client.get(reverse('calendar'), {'year': 2026, 'month': 10})
        self.assertEqual(resp.status_code, 200)
        events_by_day = resp.context['events_by_day']
        day_31_ids = [e.id for e in events_by_day.get(31, [])]
        self.assertIn(self.late_event.id, day_31_ids)

    def test_late_event_does_not_also_leak_into_next_month_view(self):
        resp = self.client.get(reverse('calendar'), {'year': 2026, 'month': 11})
        self.assertEqual(resp.status_code, 200)
        events_by_day = resp.context['events_by_day']
        all_ids = [e.id for events in events_by_day.values() for e in events]
        self.assertNotIn(self.late_event.id, all_ids)

    def test_control_event_on_the_30th_still_shows(self):
        """Sanity check the fix didn't just move the bug — the 30th keeps working."""
        resp = self.client.get(reverse('calendar'), {'year': 2026, 'month': 10})
        events_by_day = resp.context['events_by_day']
        day_30_ids = [e.id for e in events_by_day.get(30, [])]
        self.assertIn(self.control_event.id, day_30_ids)

    def test_api_agrees_with_the_page(self):
        resp = self.client.get(reverse('calendar_data_api'), {'year': 2026, 'month': 10})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        # JSON round-trips int dict keys as strings.
        day_31 = data['events'].get('31', [])
        self.assertTrue(any(e['id'] == self.late_event.id for e in day_31))

        resp_nov = self.client.get(reverse('calendar_data_api'), {'year': 2026, 'month': 11})
        data_nov = json.loads(resp_nov.content)
        all_ids_nov = [e['id'] for day_events in data_nov['events'].values() for e in day_events]
        self.assertNotIn(self.late_event.id, all_ids_nov)


class CalendarFebruaryBoundaryTests(TestCase):
    """Same bug, worse case: January (31 days) rolling into a 28/29-day February."""

    def setUp(self):
        self.user = make_member('cal-member-2')
        self.client = Client()
        self.client.force_login(self.user)
        self.late_event = Event.objects.create(
            title='Late January Meeting',
            description='D',
            date_time=timezone.make_aware(datetime(2026, 1, 31, 23, 55)),
            is_active=True,
            created_by=self.user,
        )

    def test_late_january_event_shows_in_january_not_february(self):
        resp = self.client.get(reverse('calendar'), {'year': 2026, 'month': 1})
        events_by_day = resp.context['events_by_day']
        day_31_ids = [e.id for e in events_by_day.get(31, [])]
        self.assertIn(self.late_event.id, day_31_ids)

        resp_feb = self.client.get(reverse('calendar'), {'year': 2026, 'month': 2})
        events_by_day_feb = resp_feb.context['events_by_day']
        all_ids_feb = [e.id for events in events_by_day_feb.values() for e in events]
        self.assertNotIn(self.late_event.id, all_ids_feb)
