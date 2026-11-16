"""
seed_demo_chapter (11-16-26): a made-up chapter for App Store screenshots.

It must build a chapter the app shows (Home's vote, events and announcements;
chat; songs), rebuild cleanly, remove itself, and never run on a database
with real members.

    python manage.py test src.tests.management.test_seed_demo_chapter
"""
import re
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings

from src.models import (
    Announcement, ChatMessage, Committee, Event, Legislation, ParliamentUser, Quote, Song, SongCategory,
)


def seed(*args):
    out = StringIO()
    call_command('seed_demo_chapter', *args, stdout=out)
    return out.getvalue()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class SeedDemoChapterTests(TestCase):

    def test_builds_a_chapter_the_app_shows(self):
        out = seed()
        password = re.search(r'Sign in as demo / (\S+)', out).group(1)
        self.assertEqual(ParliamentUser.objects.filter(user_id__startswith='DEMO-').count(), 24)
        self.assertTrue(all(u.email.endswith('@example.com') for u in ParliamentUser.objects.all()))
        self.assertGreaterEqual(Event.objects.count(), 7)
        self.assertEqual(Song.objects.count(), 4)
        self.assertEqual(Quote.objects.count(), 3)
        self.assertTrue(all(e.groupme_reminded_at for e in Event.objects.all()))   # nothing to post
        self.assertFalse(Announcement.objects.filter(send_email_on_publish=True).exists())

        # The demo account signs in to the app without 2FA and sees a live Home.
        api = Client()
        r = api.post('/api/mobile/v1/auth/login/', {'identifier': 'demo', 'password': password, 'platform': 'ios'},
                     content_type='application/json', HTTP_X_DEVICE_ID='demo-sim')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.json().get('mfa_required'))
        auth = {'HTTP_AUTHORIZATION': f"Bearer {r.json()['access_token']}"}
        home = api.get('/api/mobile/v1/home/', **auth).json()
        self.assertEqual([v['title'] for v in home['pending_votes']], ['Fund new chapter room furniture'])
        self.assertTrue(home['events'])
        self.assertEqual(home['announcements'][0]['title'], 'Social calendar for the rest of the semester')

    def test_runs_again_and_resets(self):
        seed()
        first = ParliamentUser.objects.count()
        seed()
        self.assertEqual(ParliamentUser.objects.count(), first)
        self.assertEqual(SongCategory.objects.count(), 2)
        self.assertEqual(Legislation.objects.count(), 2)
        seed('--reset')
        self.assertFalse(ParliamentUser.objects.exists())
        for model in (Event, Announcement, ChatMessage, Song, SongCategory, Legislation, Quote):
            self.assertFalse(model.objects.exists(), model.__name__)
        self.assertFalse(Committee.objects.filter(code__in=['SOCDEMO', 'PHILDEMO']).exists())

    def test_refuses_a_database_with_real_members(self):
        ParliamentUser.objects.create(user_id='R-1', username='real', name='Real Member', member_type='Member')
        with self.assertRaises(CommandError):
            seed()
        with self.assertRaises(CommandError):
            seed('--alongside')                        # DEBUG is off (as in production)
        self.assertEqual(ParliamentUser.objects.count(), 1)
        with self.settings(DEBUG=True):
            seed('--alongside')
        self.assertTrue(ParliamentUser.objects.filter(username='real').exists())
        self.assertEqual(ParliamentUser.objects.count(), 25)
