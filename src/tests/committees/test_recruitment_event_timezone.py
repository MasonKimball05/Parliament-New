"""
Recruitment event date/time was stored in the WRONG timezone.

create_recruitment_event / edit_recruitment_event parse the <input
type="datetime-local"> value (the officer's local wall-clock time — this
chapter is America/Chicago) with django.utils.dateparse.parse_datetime,
which returns a naive datetime, and then localized it as UTC. Every
recruitment event's date_time was therefore stored 5-6 hours off from what
was actually typed in — not a month-boundary edge case like the calendar
bug, but every single recruitment event, every time.

Found while investigating a different, reported bug (calendar events near
month-end vanishing) — same anti-pattern (pytz.timezone('UTC') on a value
that should use the chapter's real timezone), different call site.

Fix: use timezone.make_aware(naive), matching how EventForm/the ORM handle
every other date_time field in the app.
"""
from datetime import datetime

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Committee, ParliamentUser, RecruitmentEvent


def make_admin(uid='recruit-admin'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Recruit Admin', username=uid, member_type='Officer',
        member_status='Active', is_admin=True,
    )


def make_recruitment_committee(code='recruit'):
    return Committee.objects.create(
        name='Recruitment', code=code, is_active=True,
        is_recruitment_committee=True,
    )


class RecruitmentEventTimezoneTests(TestCase):
    def setUp(self):
        self.admin = make_admin()
        self.committee = make_recruitment_committee()
        self.client = Client()
        self.client.force_login(self.admin)

    def test_created_event_preserves_the_entered_local_time(self):
        """
        The <input type="datetime-local"> sends a bare local string with no
        timezone of its own — e.g. "2026-10-15T19:00" means 7:00 PM in
        whatever timezone the chapter runs in, never 7:00 PM UTC. Round-trip
        through localtime() and the entered wall-clock time must come back
        unchanged.
        """
        resp = self.client.post(
            reverse('create_recruitment_event', args=[self.committee.code]),
            {
                'title': 'Fall Rush Info Session',
                'date_time': '2026-10-15T19:00',
                'location': 'Union Ballroom',
                'description': '',
                'event_type': 'other',
                'visibility': 'public',
                'status': 'planned',
                'notes': '',
                'notes_visibility': 'committee_only',
                'attendance_type': 'none',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        re = RecruitmentEvent.objects.get(committee=self.committee, event__title='Fall Rush Info Session')
        local_dt = timezone.localtime(re.event.date_time)
        self.assertEqual(local_dt.strftime('%Y-%m-%dT%H:%M'), '2026-10-15T19:00')

    def test_edited_event_preserves_the_entered_local_time(self):
        from src.models import Event
        event = Event.objects.create(
            title='Original', description='', date_time=timezone.now(),
            created_by=self.admin, is_active=True,
        )
        re = RecruitmentEvent.objects.create(event=event, committee=self.committee)

        resp = self.client.post(
            reverse('edit_recruitment_event', args=[self.committee.code, re.id]),
            {
                'title': 'Original',
                'date_time': '2026-11-30T20:30',
                'location': '',
                'description': '',
                'event_type': 'other',
                'visibility': 'public',
                'status': 'planned',
                'notes': '',
                'notes_visibility': 'committee_only',
                'attendance_type': 'none',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        event.refresh_from_db()
        local_dt = timezone.localtime(event.date_time)
        self.assertEqual(local_dt.strftime('%Y-%m-%dT%H:%M'), '2026-11-30T20:30')

    def test_created_event_is_not_off_by_the_utc_offset(self):
        """
        Direct regression pin for the actual bug: under the old code, a 7 PM
        entry was stored as 7 PM UTC — several hours off from 7 PM Central.
        Assert the stored UTC hour is NOT what the old (broken) behavior
        would have produced.
        """
        resp = self.client.post(
            reverse('create_recruitment_event', args=[self.committee.code]),
            {
                'title': 'Offset Check Event',
                'date_time': '2026-10-15T19:00',
                'location': '', 'description': '',
                'event_type': 'other', 'visibility': 'public', 'status': 'planned',
                'notes': '', 'notes_visibility': 'committee_only',
                'attendance_type': 'none',
            },
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        re = RecruitmentEvent.objects.get(committee=self.committee, event__title='Offset Check Event')
        # Old buggy behavior: stored UTC hour == 19 (treated "19:00" as if it
        # were already UTC). Correct behavior: UTC hour is 19 plus the
        # chapter's UTC offset (5 or 6 hours ahead, i.e. 0 or 1 the next day).
        self.assertNotEqual(re.event.date_time.hour, 19)
