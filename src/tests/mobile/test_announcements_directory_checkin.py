"""
Mobile plan, Phase 3b — Announcements, Directory and QR check-in endpoints.

The app must see the announcements and members the web shows, under the same
flag / toggle / pledge rules, and a check-in from the app must go through the
same code as a scanned QR code on the website.

    python manage.py test src.tests.mobile.test_announcements_directory_checkin
"""
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    Announcement, Attendance, Event, EventCheckinWindow, UserAnnouncementView,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('p3b-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, path, access=None, **params):
        return self.api.get(API + path, params, **bearer(access or self.access))

    def post(self, path, data, access=None):
        return self.api.post(API + path, data, content_type='application/json',
                             **bearer(access or self.access))


class AnnouncementTests(_Base):

    def announce(self, title, **kw):
        return Announcement.objects.create(title=title, content=f'{title} text', posted_by=self.user, **kw)

    def test_lists_what_the_web_lists_newest_first(self):
        self.announce('Old')
        self.announce('New')
        self.announce('Hidden', is_active=False)
        self.announce('Scheduled', publish_at=timezone.now() + timedelta(days=1))
        self.announce('Officers only', visible_to=['Officer'])
        body = self.get('announcements/').json()
        self.assertEqual([a['title'] for a in body['announcements']], ['New', 'Old'])
        self.assertIsNone(body['next_before'])

    def test_pages_with_before(self):
        for i in range(5):
            self.announce(f'A{i}')
        first = self.get('announcements/', limit=2).json()
        self.assertEqual([a['title'] for a in first['announcements']], ['A4', 'A3'])
        second = self.get('announcements/', limit=2, before=first['next_before']).json()
        self.assertEqual([a['title'] for a in second['announcements']], ['A2', 'A1'])

    def test_search(self):
        self.announce('Formal')
        self.announce('Dues')
        body = self.get('announcements/', q='form').json()
        self.assertEqual([a['title'] for a in body['announcements']], ['Formal'])

    def test_records_a_view_like_the_web_page(self):
        a = self.announce('Seen')
        self.get('announcements/')
        self.assertTrue(UserAnnouncementView.objects.filter(user=self.user, announcement=a).exists())

    def test_detail_hides_what_the_member_cannot_see(self):
        visible = self.announce('Mine')
        hidden = self.announce('Officers only', visible_to=['Officer'])
        self.assertEqual(self.get(f'announcements/{visible.id}/').json()['announcement']['title'], 'Mine')
        self.assertEqual(self.get(f'announcements/{hidden.id}/').status_code, 404)

    def test_flag_and_toggle(self):
        self.announce('X')
        flag('announcements', False)
        self.assertEqual(self.get('announcements/').json()['error'], 'feature_disabled')
        flag('announcements', True)
        toggle('announcements', False)
        self.assertEqual(self.get('announcements/').json()['error'], 'page_disabled')

    def test_announcement_links_open_the_native_screen(self):
        a = self.announce('Linked')
        item = self.get('home/').json()['announcements'][0]
        self.assertEqual(item['target']['screen'], 'announcements')
        self.assertEqual(item['target']['id'], a.id)


class DirectoryTests(_Base):

    def test_lists_active_members_by_group(self):
        make_user('p3b-off', name='Zed Officer', member_type='Officer', role_number='12')
        make_user('p3b-alum', name='Al Alum', member_status='Alumni')
        make_user('p3b-gone', name='Gone', member_status='Removed')
        body = self.get('members/').json()
        names = [m['name'] for m in body['members']]
        self.assertEqual(names[0], 'Zed Officer')          # officers first
        self.assertIn('User p3b-1', names)
        self.assertNotIn('Al Alum', names)
        self.assertNotIn('Gone', names)
        self.assertIn('Al Alum', [m['name'] for m in self.get('members/', alumni='1').json()['members']])

    def test_pledges_see_no_roll_numbers(self):
        make_user('p3b-off', name='Officer', member_type='Officer', role_number='12')
        pledge = make_user('p3b-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        rows = {m['user_id']: m for m in self.get('members/', access=access).json()['members']}
        self.assertIsNone(rows['p3b-off']['role_number'])
        self.assertEqual(self.get('members/').json()['members'][0]['role_number'], '12')

    def test_card_is_the_web_card(self):
        make_user('p3b-big', name='Big Brother', phone_number='2055550100')
        body = self.get('members/p3b-big/').json()['member']
        self.assertEqual(body['name'], 'Big Brother')
        self.assertEqual(body['phone'], '2055550100')
        self.assertIsNone(body['photo'])
        self.assertEqual(self.get('members/nobody/').status_code, 404)

    def test_pledge_restriction_applies(self):
        pledge = make_user('p3b-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('member_directory', phases=())
        self.assertEqual(self.get('members/', access=access).json()['error'], 'pledge_restricted')

    def test_photo_needs_the_bearer_and_a_picture(self):
        self.assertEqual(self.get('members/p3b-1/photo/').status_code, 404)
        self.user.profile_picture = SimpleUploadedFile('me.png', b'\x89PNG fake', content_type='image/png')
        self.user.save(update_fields=['profile_picture'])
        response = self.get('members/p3b-1/photo/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(self.api.get(API + 'members/p3b-1/photo/').status_code, 401)
        self.user.profile_picture.delete(save=False)


class CheckinTests(_Base):

    def setUp(self):
        super().setUp()
        flag('qr_attendance_checkin', True)
        self.officer = make_user('p3b-officer', member_type='Officer')
        self.event = Event.objects.create(title='Chapter', description='d', created_by=self.officer,
                                          date_time=timezone.now(), requires_attendance=True)
        self.window = EventCheckinWindow.open_for(self.event, opened_by=self.officer)

    def checkin(self, token=None, event=None, access=None):
        return self.post(f'events/{(event or self.event).id}/checkin/',
                         {'token': token or self.window.token}, access=access)

    def test_marks_the_member_present(self):
        response = self.checkin()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['already_checked_in'])
        row = Attendance.objects.get(event=self.event, user=self.user)
        self.assertEqual(row.status, 'present')
        self.assertTrue(self.checkin().json()['already_checked_in'])

    def test_bad_or_expired_token(self):
        self.assertEqual(self.checkin(token='nope').status_code, 410)
        self.window.expires_at = timezone.now() - timedelta(seconds=1)
        self.window.save(update_fields=['expires_at'])
        response = self.checkin()
        self.assertEqual((response.status_code, response.json()['error']), (410, 'checkin_expired'))
        self.assertFalse(Attendance.objects.filter(event=self.event, user=self.user).exists())

    def test_finalized_and_not_eligible(self):
        alum = make_user('p3b-alum', member_status='Alumni')
        _, access, _ = make_device(alum)
        self.assertEqual(self.checkin(access=access).json()['error'], 'checkin_not_eligible')
        Event.objects.filter(pk=self.event.pk).update(attendance_finalized=True)
        self.assertEqual(self.checkin().json()['error'], 'checkin_finalized')

    def test_flag_off_and_events_without_attendance(self):
        plain = Event.objects.create(title='Social', description='d', created_by=self.officer,
                                     date_time=timezone.now())
        self.assertEqual(self.checkin(event=plain).status_code, 404)
        flag('qr_attendance_checkin', False)
        self.assertEqual(self.checkin().json()['error'], 'feature_disabled')

    def test_token_required(self):
        response = self.post(f'events/{self.event.id}/checkin/', {})
        self.assertEqual(response.status_code, 400)
