"""
The officer portal's Mobile App page (10-30-26): who gets the app's pushes,
location check-in (events with and without an area, saved places) and app
versions.

    python manage.py test src.tests.officer.test_mobile_app
"""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import ActivityLog, CheckinPlace, Event, UserPreferences
from src.models.mobile import MobileDevice
from src.tests.mobile.sess_helpers import make_device, make_user


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class MobileAppTests(TestCase):

    def setUp(self):
        cache.clear()
        self.officer = make_user('an-off', member_type='Officer')
        self.on = make_user('an-on', name='Ann On')
        device, _, _ = make_device(self.on)
        device.push_token = 'ExponentPushToken[abc]'
        device.save(update_fields=['push_token'])
        prefs, _ = UserPreferences.objects.get_or_create(user=self.on)
        prefs.prefs = {'push': {'chat': False, 'events': False}}
        prefs.save()

        self.off = make_user('an-off2', name='Ollie Off')
        make_device(self.off)                                   # signed in, no token

        self.gone = make_user('an-gone', name='Gus Gone')
        d, _, _ = make_device(self.gone)
        d.push_token = 'ExponentPushToken[old]'
        d.save(update_fields=['push_token'])
        self.gone.set_password('new-password-123')              # signs the device out
        self.gone.save()

        self.none = make_user('an-none', name='Nia None')
        make_user('an-alum', name='Al Alumnus', member_status='Alumni')   # not listed
        self.client.force_login(self.officer)

    def rows(self, **params):
        r = self.client.get(reverse('mobile_app'), params)
        self.assertEqual(r.status_code, 200)
        return {row['member'].pk: row for row in r.context['rows']}, r

    def test_states_and_muted_kinds(self):
        rows, r = self.rows()
        self.assertEqual(rows['an-on']['state'], 'on')
        self.assertEqual(rows['an-on']['muted'], ['Events', 'Chat'])
        self.assertEqual(rows['an-off2']['state'], 'off')
        self.assertEqual(rows['an-gone']['state'], 'signed_out')
        self.assertEqual(rows['an-gone']['devices'], [])
        self.assertEqual(rows['an-none']['state'], 'none')
        self.assertNotIn('an-alum', rows)
        counts = {s['key']: s['count'] for s in r.context['states']}
        self.assertEqual(counts['on'], 1)
        # Read-only: the signed-out device keeps its token (the push sender clears it).
        self.assertTrue(MobileDevice.objects.get(user=self.gone).push_token)

    def test_filter(self):
        rows, _ = self.rows(tab='notifications', state='off')
        self.assertEqual(set(rows), {'an-off2'})

    def test_members_are_refused(self):
        self.client.force_login(self.none)
        r = self.client.get(reverse('mobile_app'))
        self.assertNotEqual(r.status_code, 200)
        place = CheckinPlace.objects.create(name='House', latitude=Decimal('1'), longitude=Decimal('2'))
        self.client.post(reverse('mobile_app_place', args=[place.id]), {'delete': '1'})
        self.assertTrue(CheckinPlace.objects.filter(pk=place.pk).exists())

    def test_location_tab_and_places(self):
        soon = timezone.now() + timedelta(days=2)
        with_area = Event.objects.create(title='Chapter', description='d', created_by=self.officer, date_time=soon,
                                         requires_attendance=True, checkin_latitude=Decimal('40.1'),
                                         checkin_longitude=Decimal('-88.2'), checkin_radius_m=80)
        without = Event.objects.create(title='Retreat', description='d', created_by=self.officer, date_time=soon,
                                       requires_attendance=True)
        r = self.client.get(reverse('mobile_app'), {'tab': 'location'})
        self.assertEqual(r.status_code, 200)
        areas = {row['event'].pk: row['has_area'] for row in r.context['events']}
        self.assertEqual(areas, {with_area.pk: True, without.pk: False})
        self.assertEqual(r.context['without_area'], 1)

        house = CheckinPlace.objects.create(name='House', latitude=Decimal('40.1'), longitude=Decimal('-88.2'))
        CheckinPlace.objects.create(name='Library', latitude=Decimal('40.2'), longitude=Decimal('-88.3'))
        url = reverse('mobile_app_place', args=[house.id])
        self.client.post(url, {'name': 'Library'})                        # taken: refused
        house.refresh_from_db()
        self.assertEqual(house.name, 'House')
        self.client.post(url, {'name': 'The House'})
        house.refresh_from_db()
        self.assertEqual(house.name, 'The House')
        self.client.post(url, {'delete': '1'})
        self.assertFalse(CheckinPlace.objects.filter(pk=house.pk).exists())
        with_area.refresh_from_db()
        self.assertEqual(with_area.checkin_radius_m, 80)                  # the event keeps its area
        self.assertTrue(ActivityLog.objects.filter(object_type='CheckinPlace').exists())

    def test_versions_tab(self):
        r = self.client.get(reverse('mobile_app'), {'tab': 'versions'})
        self.assertEqual(r.status_code, 200)
        versions = {(v['platform'], v['version']): v['members'] for v in r.context['versions']}
        self.assertEqual(versions[('iOS', '1.0.0')], ['Ann On', 'Ollie Off'])   # signed-out phones aren't counted
