"""
11-17-26: the event form's "more settings" in the app — repeating, the second
reminder and email copies, the check-in area, study hours and GroupMe —
through EventForm and create_event's own steps (src/mobile/views_officer.py).
"""
from datetime import timedelta

from django.utils import timezone

from src.models import CheckinPlace, Event, GroupMeChannel
from src.tests.mobile.test_home_and_notifications import flag
from src.tests.mobile.test_officer import _Base


class EventMoreSettingsTests(_Base):

    def setUp(self):
        super().setUp()
        flag('location_checkin', True)
        self.when = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
        self.brothers = GroupMeChannel.objects.create(
            name='Brothers', group_id='111', bot_id='botBROTHERS1', audience=['Member'])
        self.officers = GroupMeChannel.objects.create(
            name='Officers', group_id='222', bot_id='botOFFICERS1', audience=['Officer'])

    def create(self, **body):
        return self.call('post', 'officer/events/', {
            'title': 'Chapter', 'description': 'Weekly', 'date_time': self.when.isoformat(), **body})

    def test_options_list_places_channels_and_choices(self):
        CheckinPlace.objects.create(name='House', latitude='33.5', longitude='-86.8', radius_m=60)
        body = self.call('get', 'officer/events/').json()
        self.assertTrue(body['checkin']['enabled'])
        self.assertEqual(body['checkin']['places'][0]['name'], 'House')
        self.assertEqual((body['checkin']['min_radius_m'], body['checkin']['max_radius_m']), (25, 1000))
        self.assertEqual([c['name'] for c in body['groupme']['channels']], ['Brothers', 'Officers'])
        self.assertIn('weekly', [t['value'] for t in body['recurrence_types']])

    def test_create_a_weekly_series(self):
        r = self.create(recurrence_type='weekly', recurrence_days=[self.when.weekday()],
                        recurrence_end_date=(self.when + timedelta(weeks=3)).date().isoformat())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertGreaterEqual(r.json()['occurrences'], 2)
        root = Event.objects.get(pk=r.json()['event']['id'])
        self.assertTrue(root.is_recurring)
        self.assertEqual(Event.objects.filter(parent_event=root).count(), r.json()['occurrences'])

    def test_reminders_and_check_in_area_saved_as_a_place(self):
        r = self.create(reminder_2_enabled=True, reminder_2_hours_before=2, reminder_2_email_enabled=True,
                        requires_attendance=True, checkin_latitude=33.51, checkin_longitude=-86.81,
                        checkin_radius_m=80, checkin_save_as='Chapter room', checkin_min_minutes=30)
        self.assertEqual(r.status_code, 201, r.content)
        e = r.json()['event']
        self.assertEqual((e['reminder_2_enabled'], e['reminder_2_hours_before']), (True, 2))
        self.assertEqual(e['checkin']['radius_m'], 80)
        self.assertEqual(e['checkin_min_minutes'], 30)
        self.assertTrue(CheckinPlace.objects.filter(name='Chapter room', radius_m=80).exists())

    def test_check_in_area_off_and_bad_radius(self):
        r = self.create(checkin_latitude=33.5, checkin_longitude=-86.8, checkin_radius_m=60)
        eid = r.json()['event']['id']
        r = self.call('patch', f'officer/events/{eid}/', {'checkin_radius_m': None})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()['event']['checkin'])
        r = self.call('patch', f'officer/events/{eid}/', {
            'checkin_latitude': 33.5, 'checkin_longitude': -86.8, 'checkin_radius_m': 5000})
        self.assertEqual(r.status_code, 400)
        self.assertIn('1000', r.json()['detail'])

    def test_study_hours_need_an_area(self):
        r = self.create(is_study_hours=True)
        self.assertEqual(r.status_code, 400)
        r = self.create(is_study_hours=True, checkin_latitude=33.5, checkin_longitude=-86.8, checkin_radius_m=60,
                        duration_minutes=120, requires_attendance=True)
        self.assertEqual(r.status_code, 201, r.content)
        e = r.json()['event']
        self.assertEqual((e['is_study_hours'], e['duration_minutes'], e['requires_attendance']), (True, 120, False))

    def test_groupme_channels_follow_the_audience_rule(self):
        r = self.create(visible_to=['Member'],
                        groupme={'channel_ids': [self.brothers.id, self.officers.id], 'hours': 6})
        self.assertEqual(r.status_code, 201, r.content)
        e = Event.objects.get(pk=r.json()['event']['id'])
        self.assertEqual(list(e.groupme_channels.all()), [self.brothers])   # Officers isn't Member-only
        self.assertEqual(e.groupme_reminder_hours, 6)
        # An edit without `groupme` leaves the choice alone; with it, replaces it.
        self.call('patch', f'officer/events/{e.id}/', {'location': 'House'})
        self.assertEqual(list(e.groupme_channels.all()), [self.brothers])
        self.call('patch', f'officer/events/{e.id}/', {'groupme': {'channel_ids': [], 'hours': 24}})
        self.assertEqual(e.groupme_channels.count(), 0)

    def test_an_edit_does_not_touch_the_series_rule(self):
        r = self.create(recurrence_type='weekly', recurrence_days=[self.when.weekday()],
                        recurrence_end_date=(self.when + timedelta(weeks=2)).date().isoformat())
        eid = r.json()['event']['id']
        self.call('patch', f'officer/events/{eid}/', {'recurrence_type': 'none', 'location': 'X'})
        self.assertEqual(Event.objects.get(pk=eid).recurrence_type, 'weekly')
