"""
v3.60.0 — location check-in (src/location_checkin.py, src/mobile/views_location.py).

A member's phone inside an event's check-in area, inside its window, marks
them present (counted like a QR scan). The check-in endpoint authenticates
with the device's narrow check-in key, which must die with the device and do
nothing else. Officers set areas in the app or on the event form.

    python manage.py test src.tests.mobile.test_location_checkin
"""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src import location_checkin as lc
from src.forms.events import EventForm
from src.models import Attendance, CheckinPlace, Event
from src.models.mobile import MobileDevice
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'
SPOT = (Decimal('33.501000'), Decimal('-86.801000'))
# About 60 m north of SPOT (0.00054° of latitude).
NEAR = (33.50154, -86.801)
# About 1.1 km north.
FAR = (33.511, -86.801)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        flag('location_checkin', True)
        self.user = make_user('loc-1')
        self.device, self.access, _ = make_device(self.user)
        self.officer = make_user('loc-off', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.api = Client()
        self.event = self.meeting()

    def meeting(self, start=None, **kw):
        return Event.objects.create(
            title='Chapter', description='d', created_by=self.officer, requires_attendance=True,
            date_time=start or timezone.now(), checkin_latitude=SPOT[0], checkin_longitude=SPOT[1],
            checkin_radius_m=100, **kw)

    def key(self, access=None):
        r = self.api.post(API + 'devices/checkin-key/', **bearer(access or self.access))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['key']

    def checkin(self, point=NEAR, accuracy=10, key=None, event=None):
        return self.api.post(API + f'events/{(event or self.event).id}/checkin/location/',
                             {'latitude': point[0], 'longitude': point[1], 'accuracy': accuracy},
                             content_type='application/json',
                             HTTP_X_CHECKIN_KEY=key if key is not None else self.key())


class RulesTests(_Base):

    def test_distance(self):
        self.assertAlmostEqual(lc.distance_m(SPOT[0], SPOT[1], *NEAR), 60, delta=2)

    def test_window_is_an_hour_before_to_three_after(self):
        opens, closes = lc.window(self.event)
        self.assertEqual(self.event.date_time - opens, timedelta(hours=1))
        self.assertEqual(closes - self.event.date_time, timedelta(hours=3))

    def test_clean_point_bounds(self):
        with self.assertRaises(ValueError):
            lc.clean_point(91, 0, 100)
        with self.assertRaises(ValueError):
            lc.clean_point(33, -86, 5)
        self.assertEqual(lc.clean_point('33.5', '-86.8', '100')[2], 100)


class CheckinTests(_Base):

    def test_inside_the_area_checks_in(self):
        r = self.checkin()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json()['already_checked_in'])
        row = Attendance.objects.get(event=self.event, user=self.user)
        self.assertEqual(row.status, 'present')
        self.assertIn('by location', row.notes)
        self.assertTrue(self.checkin().json()['already_checked_in'])

    def test_too_far_inaccurate_and_outside_the_window(self):
        self.assertEqual(self.checkin(point=FAR).json()['error'], 'checkin_too_far')
        self.assertEqual(self.checkin(accuracy=400).json()['error'], 'checkin_inaccurate')
        later = self.meeting(start=timezone.now() + timedelta(hours=2))
        self.assertEqual(self.checkin(event=later).json()['error'], 'checkin_not_open')
        earlier = self.meeting(start=timezone.now() - timedelta(hours=4))
        self.assertEqual(self.checkin(event=earlier).json()['error'], 'checkin_not_open')
        self.assertFalse(Attendance.objects.filter(user=self.user).exists())

    def test_gps_uncertainty_is_allowed_for_up_to_fifty_meters(self):
        self.event.checkin_radius_m = 40
        self.event.save()
        self.assertEqual(self.checkin(accuracy=5).json()['error'], 'checkin_too_far')   # 60 m > 40 + 5
        self.assertEqual(self.checkin(accuracy=30).status_code, 200)                     # 60 m - 30 <= 40

    def test_never_overwrites_an_officers_present_or_late(self):
        Attendance.objects.create(event=self.event, user=self.user, attendance_type='event',
                                  status='late', marked_by=self.officer)
        self.assertTrue(self.checkin().json()['already_checked_in'])
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, 'late')

    def test_never_overwrites_an_officers_absent_or_excused(self):
        # 10-28-26: an automatic four-hour check-in used to flip "absent" back to present.
        for status in ('absent', 'excused'):
            Attendance.objects.update_or_create(event=self.event, user=self.user, attendance_type='event',
                                                defaults={'status': status, 'marked_by': self.officer})
            r = self.checkin()
            self.assertEqual(r.json()['error'], 'checkin_already_marked')
            self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, status)

    def test_a_pending_row_is_filled_in(self):
        Attendance.objects.create(event=self.event, user=self.user, attendance_type='event',
                                  status='pending', marked_by=self.officer)
        self.assertEqual(self.checkin().status_code, 200)
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.user).status, 'present')

    def test_hidden_and_archived_events_are_not_found(self):
        # 10-28-26: the lookup ignored visibility, so a hidden event could be located
        # from the too_far distances and checked in to.
        key = self.key()
        hidden = self.meeting(visible_to=['Officer'])
        for point in (FAR, NEAR):
            r = self.checkin(point=point, event=hidden, key=key)
            self.assertEqual(r.status_code, 404, r.content)
            self.assertNotIn('distance_m', r.json())
        archived = self.meeting(archived=True)
        self.assertEqual(self.checkin(event=archived, key=key).status_code, 404)
        self.assertFalse(Attendance.objects.filter(user=self.user).exists())

    def test_non_finite_and_out_of_range_numbers_are_refused(self):
        key = self.key()
        for lat, lng, acc in (('nan', NEAR[1], 5), (NEAR[0], 'inf', 5), (91, NEAR[1], 5)):
            r = self.api.post(API + f'events/{self.event.id}/checkin/location/',
                              {'latitude': lat, 'longitude': lng, 'accuracy': acc},
                              content_type='application/json', HTTP_X_CHECKIN_KEY=key)
            self.assertEqual(r.status_code, 400, (lat, lng, r.content))
        for acc in ('nan', 'inf', -1):
            self.assertEqual(self.checkin(accuracy=acc, key=key).json()['error'], 'checkin_inaccurate')
        self.assertFalse(Attendance.objects.filter(user=self.user).exists())

    def test_finalized_and_inactive_members(self):
        Event.objects.filter(pk=self.event.pk).update(attendance_finalized=True)
        self.assertEqual(self.checkin().json()['error'], 'checkin_finalized')
        alum = make_user('loc-alum', member_status='Alumni')
        _, access, _ = make_device(alum)
        other = self.meeting()
        # An alumnus can't even get a key's check-in through.
        r = self.checkin(event=other, key=self.key(access))
        self.assertIn(r.status_code, (401, 403))

    def test_flag_off(self):
        key = self.key()
        flag('location_checkin', False)
        self.assertEqual(self.checkin(key=key).json()['error'], 'feature_disabled')


class KeyTests(_Base):

    def test_only_the_hash_is_stored_and_a_new_key_replaces_the_old(self):
        first = self.key()
        self.device.refresh_from_db()
        self.assertNotIn(first, self.device.checkin_key_hash)
        second = self.key()
        self.assertEqual(self.checkin(key=first).status_code, 401)
        self.assertEqual(self.checkin(key=second).status_code, 200)

    def test_a_revoked_phone_loses_its_key(self):
        key = self.key()
        MobileDevice.objects.filter(pk=self.device.pk).update(revoked_at=timezone.now())
        self.assertEqual(self.checkin(key=key).status_code, 401)

    def test_the_key_opens_nothing_else(self):
        key = self.key()
        r = self.api.get(API + 'me/', HTTP_X_CHECKIN_KEY=key)
        self.assertEqual(r.status_code, 401)
        r = self.api.get(API + 'events/checkin-areas/', HTTP_X_CHECKIN_KEY=key)
        self.assertEqual(r.status_code, 401)

    def test_a_bearer_token_is_not_a_check_in_key(self):
        r = self.api.post(API + f'events/{self.event.id}/checkin/location/',
                          {'latitude': NEAR[0], 'longitude': NEAR[1], 'accuracy': 5},
                          content_type='application/json', **bearer(self.access))
        self.assertEqual(r.status_code, 401)


class AreaListTests(_Base):

    def test_lists_upcoming_areas_with_their_windows(self):
        self.meeting(start=timezone.now() + timedelta(days=3))
        self.meeting(start=timezone.now() + timedelta(days=9))                   # too far ahead
        Event.objects.create(title='No area', description='d', created_by=self.officer,
                             requires_attendance=True, date_time=timezone.now() + timedelta(days=1))
        self.meeting(start=timezone.now() + timedelta(days=2), visible_to=['Officer'])
        body = self.api.get(API + 'events/checkin-areas/', **bearer(self.access)).json()
        self.assertTrue(body['enabled'])
        self.assertEqual(len(body['events']), 2)
        first = body['events'][0]
        self.assertEqual(first['id'], self.event.id)
        self.assertEqual(first['radius_m'], 100)
        self.assertFalse(first['checked_in'])

    def test_says_when_already_checked_in_and_when_off(self):
        self.checkin()
        body = self.api.get(API + 'events/checkin-areas/', **bearer(self.access)).json()
        self.assertTrue(body['events'][0]['checked_in'])
        flag('location_checkin', False)
        body = self.api.get(API + 'events/checkin-areas/', **bearer(self.access)).json()
        self.assertEqual(body, {'enabled': False, 'events': []})


class OfficerTests(_Base):

    def put(self, data, access=None, event=None):
        return self.api.put(API + f'events/{(event or self.event).id}/checkin-area/', data,
                            content_type='application/json', **bearer(access or self.officer_access))

    def test_officer_sets_and_clears_an_area_and_saves_a_place(self):
        plain = Event.objects.create(title='Plain', description='d', created_by=self.officer,
                                     requires_attendance=True, date_time=timezone.now())
        r = self.put({'latitude': 33.5, 'longitude': -86.8, 'radius_m': 50, 'save_as': 'Chapter room'}, event=plain)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['checkin_area']['radius_m'], 50)
        place = CheckinPlace.objects.get(name='Chapter room')
        other = Event.objects.create(title='Next', description='d', created_by=self.officer,
                                     requires_attendance=True, date_time=timezone.now())
        self.assertEqual(self.put({'place_id': place.id}, event=other).json()['checkin_area']['radius_m'], 50)
        r = self.api.delete(API + f'events/{plain.id}/checkin-area/', **bearer(self.officer_access))
        plain.refresh_from_db()
        self.assertIsNone(plain.checkin_radius_m)

    def test_members_cannot_set_areas_or_places(self):
        self.assertEqual(self.put({'latitude': 1, 'longitude': 1, 'radius_m': 50}, access=self.access).status_code, 403)
        r = self.api.get(API + 'checkin-places/', **bearer(self.access))
        self.assertEqual(r.status_code, 403)

    def test_bad_values(self):
        self.assertEqual(self.put({'latitude': 33.5, 'longitude': -86.8, 'radius_m': 5000}).status_code, 400)
        self.assertEqual(self.put({'latitude': 'x', 'longitude': -86.8, 'radius_m': 50}).status_code, 400)

    def test_place_list_create_delete(self):
        r = self.api.post(API + 'checkin-places/', {'name': 'House', 'latitude': 33.5, 'longitude': -86.8},
                          content_type='application/json', **bearer(self.officer_access))
        self.assertEqual(r.status_code, 201)
        pid = r.json()['place']['id']
        names = [p['name'] for p in self.api.get(API + 'checkin-places/', **bearer(self.officer_access)).json()['places']]
        self.assertEqual(names, ['House'])
        self.assertEqual(self.api.delete(API + f'checkin-places/{pid}/', **bearer(self.officer_access)).status_code, 200)

    def test_event_detail_tells_officers_they_can_set_it(self):
        body = self.api.get(API + f'events/{self.event.id}/', **bearer(self.officer_access)).json()['event']
        self.assertTrue(body['can_manage_checkin'])
        self.assertEqual(body['checkin_area']['radius_m'], 100)
        member = self.api.get(API + f'events/{self.event.id}/', **bearer(self.access)).json()['event']
        self.assertFalse(member['can_manage_checkin'])
        self.assertFalse(member['checked_in'])
        self.checkin()
        self.assertTrue(self.api.get(API + f'events/{self.event.id}/', **bearer(self.access)).json()['event']['checked_in'])


class FormTests(_Base):

    def data(self, **over):
        base = {'title': 'Meeting', 'description': 'd', 'date_time': '2026-11-05T19:00',
                'recurrence_type': 'none', 'recurrence_interval': 1, 'recurrence_unit': 'weeks',
                'requires_attendance': 'on', 'is_active': 'on',
                'reminder_1_hours_before': 24, 'reminder_2_hours_before': 1}
        base.update(over)
        return base

    def test_pin_and_radius_together(self):
        form = EventForm(self.data(checkin_latitude='33.5', checkin_longitude='-86.8', checkin_radius_m='100'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['checkin_radius_m'], 100)
        form = EventForm(self.data(checkin_radius_m='100'))
        self.assertFalse(form.is_valid())
        form = EventForm(self.data(checkin_latitude='33.5', checkin_longitude='-86.8', checkin_radius_m=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['checkin_latitude'])

    def test_flag_off_leaves_an_existing_area_alone(self):
        flag('location_checkin', False)
        form = EventForm(self.data(), instance=self.event)
        self.assertNotIn('checkin_radius_m', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.event.refresh_from_db()
        self.assertEqual(self.event.checkin_radius_m, 100)
