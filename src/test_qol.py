"""
v3.15.0 — QOL batch smoke tests (07-19-26).

Covers the server-side pieces: the Google Calendar quick-add URL, the
calendar data API's add-to-calendar fields, and the profile page's
recent-logins card. The pure-JS pieces (dirty-form guard, toast
auto-dismiss, service-worker offline fallback) have no server component to
test; template rendering below at least proves the pages still parse.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from src.models import Event, LoginHistory, ParliamentUser


class GoogleCalendarUrlTests(TestCase):
    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='q1', name='QOL User', username='q1', member_type='Officer')

    def test_url_shape_utc_times_and_escaping(self):
        event = Event.objects.create(
            title='Chapter & "Formal" Meeting',
            description='Bring your voting card.',
            date_time=datetime(2026, 9, 1, 19, 30, tzinfo=dt_timezone.utc),
            location='Great Hall, Samford',
            created_by=self.user)
        url = event.google_calendar_url
        parsed = urlparse(url)
        self.assertEqual(parsed.netloc, 'calendar.google.com')
        q = parse_qs(parsed.query)
        self.assertEqual(q['action'], ['TEMPLATE'])
        self.assertEqual(q['text'], ['Chapter & "Formal" Meeting'])  # round-trips
        # 19:30 UTC start, assumed 1-hour duration
        self.assertEqual(q['dates'], ['20260901T193000Z/20260901T203000Z'])
        self.assertEqual(q['location'], ['Great Hall, Samford'])
        # Raw specials must not appear unescaped in the query string
        self.assertNotIn(' ', parsed.query)
        self.assertNotIn('"', parsed.query)


class CalendarApiAddToCalendarTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='q2', name='Cal User', username='q2', member_type='Member')
        self.client.force_login(self.user)
        self.event = Event.objects.create(
            title='API Event', description='D',
            date_time=timezone.now() + timedelta(days=1),
            created_by=self.user)

    def test_api_payload_includes_links(self):
        now = timezone.now()
        resp = self.client.get(reverse('calendar_data_api'),
                               {'year': now.year, 'month': now.month})
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('google_url', body)
        self.assertIn('calendar.google.com', body)
        self.assertIn('ics_url', body)


class ProfileRecentLoginsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='q3', name='Login User', username='q3', member_type='Member')
        self.client.force_login(self.user)

    def test_recent_logins_render_without_raw_ip(self):
        LoginHistory.objects.create(
            user=self.user, status='success', ip_address='203.0.113.7',
            browser='Firefox 128', os='macOS', city='Birmingham', region='AL')
        LoginHistory.objects.create(  # failed attempts must not show
            user=self.user, status='failed', ip_address='203.0.113.8',
            browser='curl', os='Linux')
        resp = self.client.get(reverse('profile'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Recent Logins')
        self.assertContains(resp, 'Firefox 128')
        self.assertContains(resp, 'Birmingham')
        self.assertNotContains(resp, 'curl')
        self.assertNotContains(resp, '203.0.113.7')  # IP never rendered

    def test_empty_history_has_friendly_state(self):
        resp = self.client.get(reverse('profile'))
        self.assertContains(resp, 'No login history recorded yet')
