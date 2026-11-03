"""
v4.4.3 — the check-in and poll QR codes in the app (src/mobile/views_qr.py),
on the same code path as the website's manage_qr_checkin / manage_poll_qr.

    python manage.py test src.tests.mobile.test_qr
"""
import base64
import io
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from src.models import (
    ActivityLog, Announcement, AnnouncementPoll, AnnouncementPollEmbed, Attendance, Event, EventCheckinEmbed,
    EventCheckinWindow,
)
from src.tests.mobile.test_home_and_notifications import flag
from src.utils.qr_svg import render_qr_png_data_uri
from src.tests.mobile.test_officer import _Base

PNG_PREFIX = 'data:image/png;base64,'


def png(data_uri):
    """The decoded image, after checking it's a PNG data URI."""
    assert data_uri.startswith(PNG_PREFIX), data_uri[:40]
    img = Image.open(io.BytesIO(base64.b64decode(data_uri[len(PNG_PREFIX):])))
    assert img.format == 'PNG'
    return img


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class CheckinQRTests(_Base):

    def setUp(self):
        super().setUp()
        flag('qr_attendance_checkin', True)
        self.event = Event.objects.create(title='Chapter', date_time=timezone.now(), requires_attendance=True,
                                          created_by=self.officer)
        self.path = f'officer/events/{self.event.id}/qr/'

    def test_members_and_flags_and_events_are_refused(self):
        self.assertEqual(self.call('get', self.path, access=self.member_access).status_code, 403)
        self.assertEqual(self.call('post', self.path + 'open/', {}, access=self.member_access).status_code, 403)
        self.assertEqual(self.call('post', self.path + 'embed/', {}, access=self.member_access).status_code, 403)
        self.assertFalse(EventCheckinWindow.objects.exists())
        self.assertFalse(EventCheckinEmbed.objects.exists())
        flag('qr_attendance_checkin', False)
        r = self.call('get', self.path)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        flag('qr_attendance_checkin', True)
        other = Event.objects.create(title='Social', date_time=self.event.date_time, requires_attendance=False,
                                     created_by=self.officer)
        self.assertEqual(self.call('get', f'officer/events/{other.id}/qr/').status_code, 404)
        self.assertEqual(self.call('get', self.path).status_code, 200)

    def test_open_shows_the_code_then_close(self):
        body = self.call('get', self.path).json()
        self.assertIsNone(body['window'])
        self.assertEqual(body['window_minutes'], EventCheckinWindow.WINDOW_MINUTES)
        self.assertEqual(body['checked_in'], {'qr': 0, 'present': 0})

        r = self.call('post', self.path + 'open/', {})
        self.assertEqual(r.status_code, 200, r.content)
        w = r.json()['window']
        self.assertGreater(w['seconds_left'], EventCheckinWindow.WINDOW_MINUTES * 60 - 5)
        self.assertEqual(png(w['qr']).size[0], png(w['qr']).size[1])
        window = EventCheckinWindow.get_open_window(self.event)
        self.assertEqual(window.opened_by, self.officer)
        # Logged as the website's open_qr_checkin logs it.
        self.assertTrue(ActivityLog.objects.filter(object_type='Event', object_id=self.event.id,
                                                   description__contains='QR check-in window').exists())
        # The encoded URL itself isn't sent apart from the picture.
        self.assertNotIn(window.token, r.content.decode())

        r = self.call('post', self.path + 'close/', {})
        self.assertIsNone(r.json()['window'])
        window.refresh_from_db()
        self.assertEqual(window.closed_early_by, self.officer)
        self.assertIsNone(EventCheckinWindow.get_open_window(self.event))
        # Closing with nothing open is harmless.
        self.assertEqual(self.call('post', self.path + 'close/', {}).status_code, 200)

    def test_qr_encodes_the_websites_checkin_url(self):
        self.call('post', self.path + 'open/', {})
        window = EventCheckinWindow.get_open_window(self.event)
        with patch('src.mobile.views_qr.render_qr_png_data_uri', return_value=PNG_PREFIX) as render:
            self.call('get', self.path)
        self.assertEqual(render.call_args.args[0],
                         'http://testserver' + reverse('event_qr_checkin', args=[self.event.id, window.token]))

    def test_finalized_event_cannot_open(self):
        self.event.attendance_finalized = True
        self.event.save()
        r = self.call('post', self.path + 'open/', {})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'finalized'))
        self.assertFalse(EventCheckinWindow.objects.exists())

    def test_attendance_roster_says_whether_qr_is_on(self):
        path = f'officer/events/{self.event.id}/attendance/'
        self.assertTrue(self.call('get', path).json()['event']['qr_checkin'])
        flag('qr_attendance_checkin', False)
        self.assertFalse(self.call('get', path).json()['event']['qr_checkin'])

    def test_live_count(self):
        Attendance.objects.create(event=self.event, user=self.member, attendance_type='event', status='present',
                                  notes='Self-checked in via QR (window opened by x)')
        Attendance.objects.create(event=self.event, user=self.officer, attendance_type='event', status='present')
        self.assertEqual(self.call('get', self.path).json()['checked_in'], {'qr': 1, 'present': 2})

    def test_embed_generate_revoke_matches_the_website(self):
        self.assertIsNone(self.call('get', self.path).json()['embed_url'])
        url = self.call('post', self.path + 'embed/', {}).json()['embed_url']
        embed = EventCheckinEmbed.objects.get(event=self.event)
        self.assertTrue(url.endswith(reverse('event_checkin_embed_image', args=[self.event.id, embed.token])))
        # Reused, not reissued.
        self.assertEqual(self.call('post', self.path + 'embed/', {}).json()['embed_url'], url)
        self.assertEqual(self.call('get', self.path).json()['embed_url'], url)
        # The website's page shows the same link.
        self.client.force_login(self.officer)
        page = self.client.get(reverse('manage_qr_checkin', args=[self.event.id]))
        self.assertEqual(page.context['embed_url'], url)

        self.assertIsNone(self.call('delete', self.path + 'embed/').json()['embed_url'])
        embed.refresh_from_db()
        self.assertFalse(embed.is_active())
        self.assertIsNone(self.call('get', self.path).json()['embed_url'])
        # A fresh one is a new token.
        self.assertNotEqual(self.call('post', self.path + 'embed/', {}).json()['embed_url'], url)

    def test_web_and_app_share_the_window(self):
        # Opened on the website, shown and closed in the app; and the reverse.
        self.client.force_login(self.officer)
        self.client.post(reverse('open_qr_checkin', args=[self.event.id]))
        self.assertIsNotNone(self.call('get', self.path).json()['window'])
        self.call('post', self.path + 'close/', {})
        self.assertIsNone(EventCheckinWindow.get_open_window(self.event))
        self.call('post', self.path + 'open/', {})
        self.assertEqual(self.client.get(reverse('qr_checkin_image', args=[self.event.id])).status_code, 200)
        self.client.post(reverse('close_qr_checkin', args=[self.event.id]))
        self.assertIsNone(self.call('get', self.path).json()['window'])
        # The website still refuses a finalized event through the shared helper.
        self.event.attendance_finalized = True
        self.event.save()
        self.client.post(reverse('open_qr_checkin', args=[self.event.id]))
        self.assertIsNone(EventCheckinWindow.get_open_window(self.event))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class PollQRTests(_Base):

    def setUp(self):
        super().setUp()
        self.a = Announcement.objects.create(title='Formal', content='x', posted_by=self.officer)
        self.poll = AnnouncementPoll.objects.create(announcement=self.a, title='Coming?', created_by=self.officer)
        self.path = f'officer/announcements/{self.a.id}/poll/qr/'

    def test_refusals(self):
        self.assertEqual(self.call('get', self.path, access=self.member_access).status_code, 403)
        self.assertEqual(self.call('post', self.path + 'embed/', {}, access=self.member_access).status_code, 403)
        self.assertFalse(AnnouncementPollEmbed.objects.exists())
        bare = Announcement.objects.create(title='No poll', content='x', posted_by=self.officer)
        self.assertEqual(self.call('get', f'officer/announcements/{bare.id}/poll/qr/').status_code, 404)

    def test_shows_the_take_poll_qr(self):
        with patch('src.mobile.views_qr.render_qr_png_data_uri', wraps=render_qr_png_data_uri) as render:
            body = self.call('get', self.path).json()
        self.assertEqual(render.call_args.args[0], 'http://testserver' + reverse('take_poll', args=[self.a.id]))
        png(body['qr'])
        self.assertEqual(body['poll']['title'], 'Coming?')
        self.assertTrue(body['poll']['accepting'])
        self.poll.is_open = False
        self.poll.save()
        self.assertFalse(self.call('get', self.path).json()['poll']['accepting'])

    def test_embed_generate_revoke_matches_the_website(self):
        self.assertIsNone(self.call('get', self.path).json()['embed_url'])
        url = self.call('post', self.path + 'embed/', {}).json()['embed_url']
        embed = AnnouncementPollEmbed.objects.get(poll=self.poll)
        self.assertTrue(url.endswith(reverse('poll_qr_embed_image', args=[self.a.id, embed.token])))
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(reverse('manage_poll_qr', args=[self.a.id])).context['embed_url'], url)
        self.call('delete', self.path + 'embed/')
        embed.refresh_from_db()
        self.assertFalse(embed.is_active())
        self.assertIsNone(self.client.get(reverse('manage_poll_qr', args=[self.a.id])).context['embed_url'])
        # Revoked on the website, gone from the app too.
        self.call('post', self.path + 'embed/', {})
        self.client.post(reverse('revoke_poll_qr_embed_link', args=[self.a.id]))
        self.assertIsNone(self.call('get', self.path).json()['embed_url'])
