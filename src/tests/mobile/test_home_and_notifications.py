"""
Mobile plan, Phase 3 — ``home/`` and ``notifications/``.

Each endpoint must refuse exactly what its web page refuses (page toggle,
pledge restriction) and show exactly what it shows (the same audience rule,
own notifications only). Run with:

    python manage.py test src.tests.mobile.test_home_and_notifications
"""
from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from src.models import (
    Announcement, Event, Legislation, Notification, Vote,
)
from src.models.education import PledgePageRestriction
from src.models_feature_flags import FeatureFlag, PageToggle
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'


def flag(name, on):
    FeatureFlag.objects.update_or_create(
        name=name, defaults={'display_name': name, 'description': name, 'is_enabled': on})


def toggle(url_name, on):
    PageToggle.objects.update_or_create(
        url_name=url_name, defaults={'display_name': url_name, 'is_enabled': on})


def restrict(url_name, phases=()):
    PledgePageRestriction.objects.update_or_create(
        url_name=url_name, defaults={'allowed_phases': list(phases)})


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('home-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, path, access=None, **params):
        return self.api.get(API + path, params, **bearer(access or self.access))

    def post(self, path, access=None):
        return self.api.post(API + path, **bearer(access or self.access))

    def as_pledge(self):
        pledge = make_user('P-HOME01', member_type='Pledge')
        _, access, _ = make_device(pledge)
        return pledge, access


class NotificationsTests(_Base):

    def setUp(self):
        super().setUp()
        self.other = make_user('home-2')
        self.mine = [
            Notification.objects.create(
                recipient=self.user, notification_type='event_new',
                title=f'Mine {i}', link='/calendar/')
            for i in range(5)
        ]
        self.mine[0].is_read = True
        self.mine[0].save()
        self.theirs = Notification.objects.create(
            recipient=self.other, notification_type='security', title='Theirs')

    def test_lists_own_newest_first_with_unread_count(self):
        r = self.get('notifications/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([n['title'] for n in body['notifications']],
                         ['Mine 4', 'Mine 3', 'Mine 2', 'Mine 1', 'Mine 0'])
        self.assertEqual(body['unread_count'], 4)
        self.assertIsNone(body['next_before'])
        self.assertEqual(body['notifications'][0]['target'],
                         {'url': '/calendar/', 'screen': 'calendar', 'id': None})

    def test_pages_by_id(self):
        first = self.get('notifications/', limit=2).json()
        self.assertEqual([n['title'] for n in first['notifications']], ['Mine 4', 'Mine 3'])
        second = self.get('notifications/', limit=2, before=first['next_before']).json()
        self.assertEqual([n['title'] for n in second['notifications']], ['Mine 2', 'Mine 1'])

    def test_filters(self):
        unread = self.get('notifications/', filter='unread').json()['notifications']
        self.assertEqual(len(unread), 4)
        self.assertEqual(self.get('notifications/', filter='security').json()['notifications'], [])

    def test_unsafe_link_is_not_passed_through(self):
        Notification.objects.create(recipient=self.user, notification_type='security',
                                    title='Bad', link='//evil.example/x')
        item = self.get('notifications/').json()['notifications'][0]
        self.assertEqual(item['target']['url'], '/home/')

    def test_read_own(self):
        n = self.mine[3]
        r = self.post(f'notifications/{n.id}/read/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['unread_count'], 3)
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_read_someone_elses_is_404(self):
        r = self.post(f'notifications/{self.theirs.id}/read/')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error'], 'not_found')
        self.theirs.refresh_from_db()
        self.assertFalse(self.theirs.is_read)

    def test_read_all_touches_only_own(self):
        r = self.post('notifications/read-all/')
        self.assertEqual(r.json(), {'ok': True, 'count': 4})
        self.assertEqual(self.get('notifications/count/').json(), {'unread_count': 0})
        self.theirs.refresh_from_db()
        self.assertFalse(self.theirs.is_read)

    def test_needs_bearer_token(self):
        self.assertEqual(self.api.get(API + 'notifications/').status_code, 401)

    def test_pledge_restriction_applies(self):
        _, access = self.as_pledge()
        restrict('notifications')
        r = self.get('notifications/', access=access)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_toggle_is_ignored_like_the_web_page(self):
        # The web notifications page has no @require_page_enabled.
        toggle('notifications', False)
        self.assertEqual(self.get('notifications/').status_code, 200)


class HomeTests(_Base):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.open_bill = Legislation.objects.create(
            title='Open bill', description='d', posted_by=self.user,
            available_at=now - timedelta(hours=1), voting_starts_at=now - timedelta(hours=1))
        self.voted_bill = Legislation.objects.create(
            title='Voted bill', description='d', posted_by=self.user,
            available_at=now - timedelta(hours=2), voting_starts_at=now - timedelta(hours=2))
        Vote.objects.create(user=self.user, legislation=self.voted_bill, vote_choice='yes')
        Legislation.objects.create(
            title='Not open yet', description='d', posted_by=self.user,
            available_at=now + timedelta(days=1))

        Event.objects.create(title='Chapter', description='d', created_by=self.user,
                             date_time=now + timedelta(days=1))
        Event.objects.create(title='Officers only', description='d', created_by=self.user,
                             date_time=now + timedelta(days=2), visible_to=['Officer'])
        Event.objects.create(title='Past', description='d', created_by=self.user,
                             date_time=now - timedelta(days=1))

        Announcement.objects.create(title='Live', content='x' * 500, posted_by=self.user)
        Announcement.objects.create(title='Scheduled', content='later', posted_by=self.user,
                                    publish_at=now + timedelta(days=1))
        Notification.objects.create(recipient=self.user, notification_type='security', title='n')

    def test_shape_and_audience(self):
        r = self.get('home/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([v['title'] for v in body['pending_votes']], ['Open bill'])
        self.assertEqual(body['pending_votes'][0]['target']['screen'], 'vote')
        self.assertEqual([e['title'] for e in body['events']], ['Chapter'])
        self.assertEqual([a['title'] for a in body['announcements']], ['Live'])
        self.assertEqual(len(body['announcements'][0]['excerpt']), 280)
        self.assertEqual(body['unread']['notifications'], 1)
        self.assertEqual(body['unread']['chat'], 0)

    def test_pledges_get_no_ballots(self):
        _, access = self.as_pledge()
        self.assertIsNone(self.get('home/', access=access).json()['pending_votes'])

    def test_voting_flag_does_not_hide_ballots(self):
        # legislation_voting gates managing bills, not voting (same on the web).
        flag('legislation_voting', False)
        self.assertEqual([v['title'] for v in self.get('home/').json()['pending_votes']],
                         ['Open bill'])

    def test_vote_page_off_hides_ballots(self):
        toggle('vote', False)
        self.assertIsNone(self.get('home/').json()['pending_votes'])

    def test_chat_flag_off_hides_chat_count(self):
        flag('chats', False)
        self.assertIsNone(self.get('home/').json()['unread']['chat'])

    def test_home_toggle_off_refuses(self):
        toggle('home', False)
        r = self.get('home/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'page_disabled')

    def test_pledge_restriction_on_home_refuses(self):
        _, access = self.as_pledge()
        restrict('home', phases=['3'])
        r = self.get('home/', access=access)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_query_count_is_flat(self):
        self.get('home/')   # warm the flag / toggle / count caches
        for i in range(5):
            Announcement.objects.create(title=f'A{i}', content='c', posted_by=self.user)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.get('home/').status_code, 200)
        # 3 for the page (ballots, events, announcements; nothing per row) plus
        # 4 for authentication (device + TOTP + static + 2FA requirement), run
        # once and shared by the middlewares and DRF (check_access_token).
        self.assertLessEqual(len(ctx.captured_queries), 7,
                             '\n'.join(q['sql'][:120] for q in ctx.captured_queries))
