"""
The GroupMe bot dashboard in the app (v4.4.0, src/mobile/views_groupme.py):
the dashboard's data (never the token, bot ids masked), and each action
(connect, add, edit, move, remove, test, retry) through the website's own
functions, refused with the website's message. GroupMe itself is never
called: the same fake as src/tests/officer/test_groupme_bot.py.

The routes aren't in src/mobile/urls.py yet, so this module is its own
urlconf: the site's, plus these views under /api/mobile/v1/.

    python manage.py test src.tests.mobile.test_groupme
"""
import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path

import src.urls
from src.mobile import views_groupme as v
from src.models import ActivityLog, GroupMeChannel, GroupMeMessage, GroupMeSettings
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.officer.test_groupme_bot import TOKEN, FakeGroupMe

_routes = [
    path('officer/groupme/', v.GroupMeDashboardView.as_view(), name='mobile_groupme'),
    path('officer/groupme/settings/', v.GroupMeSettingsView.as_view(), name='mobile_groupme_settings'),
    path('officer/groupme/channels/', v.GroupMeAddChannelView.as_view(), name='mobile_groupme_add_channel'),
    path('officer/groupme/channels/<int:channel_id>/edit/', v.GroupMeEditChannelView.as_view(),
         name='mobile_groupme_edit_channel'),
    path('officer/groupme/channels/<int:channel_id>/move/', v.GroupMeMoveChannelView.as_view(),
         name='mobile_groupme_move_channel'),
    path('officer/groupme/channels/<int:channel_id>/remove/', v.GroupMeRemoveChannelView.as_view(),
         name='mobile_groupme_remove_channel'),
    path('officer/groupme/channels/<int:channel_id>/test/', v.GroupMeTestChannelView.as_view(),
         name='mobile_groupme_test_channel'),
    path('officer/groupme/messages/<int:message_id>/retry/', v.GroupMeRetryMessageView.as_view(),
         name='mobile_groupme_retry_message'),
]
urlpatterns = [path('api/mobile/v1/', include((_routes, 'mobile_groupme_test')))] + src.urls.urlpatterns

API = '/api/mobile/v1/officer/groupme/'


@override_settings(ROOT_URLCONF='src.tests.mobile.test_groupme', ALLOWED_HOSTS=['testserver', 'localhost'])
class GroupMeAppTests(TestCase):

    def setUp(self):
        cache.clear()
        self.officer = make_user('gma-off', member_type='Officer')
        _, self.access, _ = make_device(self.officer)
        self.member = make_user('gma-mem')
        _, self.member_access, _ = make_device(self.member)
        self.fake = FakeGroupMe()
        patcher = mock.patch('src.groupme.requests.request', side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)
        self.brothers = GroupMeChannel.objects.create(
            name='Brothers', group_id='111', bot_id='botBROTHERS1', audience=['Member'])
        self.api = Client()

    def get(self, access=None):
        return self.api.get(API, **bearer(access or self.access))

    def post(self, sub, body=None, access=None):
        return self.api.post(API + sub, json.dumps(body or {}), content_type='application/json',
                             **bearer(access or self.access))

    def connect(self):
        s = GroupMeSettings.get_instance()
        s.access_token, s.token_owner_name = TOKEN, 'Mason K'
        s.save()

    def test_officers_and_chairs_only(self):
        """The same rule as can_manage_groupme (officer_required)."""
        self.assertEqual(self.get(self.member_access).status_code, 403)
        self.assertEqual(self.post('channels/', {'name': 'x'}, self.member_access).status_code, 403)
        chair = make_user('gma-chair', member_type='Chair')
        _, chair_access, _ = make_device(chair)
        self.assertTrue(chair.can_manage_groupme)
        self.assertEqual(self.get(chair_access).status_code, 200)
        advisor = make_user('gma-adv', member_type='Advisor')
        _, adv_access, _ = make_device(advisor)
        self.assertFalse(advisor.can_manage_groupme)
        self.assertEqual(self.get(adv_access).status_code, 403)

    def test_dashboard_never_shows_the_token_or_bot_ids(self):
        r = self.post('settings/', {'access_token': TOKEN, 'bot_name': 'Parliament', 'include_full_text': True})
        self.assertEqual((r.status_code, r.json()['detail']), (200, 'GroupMe settings saved.'))
        r = self.get()
        self.assertNotIn(TOKEN, r.content.decode())
        self.assertNotIn('botBROTHERS1', r.content.decode())
        body = r.json()
        self.assertEqual(body['connection'], {'connected': True, 'owner_name': 'Mason K', 'bot_name': 'Parliament',
                                              'include_full_text': True})
        self.assertEqual([g['name'] for g in body['groups']], ['AM Brothers', 'AM Brothers 2027', 'AM Everyone'])
        self.assertEqual(body['channels'][0]['bot'], '…ERS1')
        self.assertEqual(body['channels'][0]['audience_labels'], ['Brothers'])
        self.assertEqual(body['used_group_ids'], ['111'])
        self.assertEqual(body['stuck']['count'], 0)

    def test_a_bad_token_is_refused_with_the_websites_message(self):
        r = self.post('settings/', {'access_token': 'wrongtoken123'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'refused')
        self.assertTrue(r.json()['detail'].startswith('Token not saved:'))
        self.assertFalse(GroupMeSettings.get_instance().has_token)
        self.assertIsNone(self.post('settings/', {'include_full_text': True}).json()['detail'])  # nothing changed

    def test_add_edit_move_test_and_remove(self):
        self.connect()
        r = self.post('channels/', {'name': 'Class of 2027', 'group_id': '333', 'audience': ['Member', 'Pledge']})
        self.assertEqual(r.status_code, 201, r.content)
        c = GroupMeChannel.objects.get(name='Class of 2027')
        self.assertEqual((c.group_name, c.bot_created_here, c.audience), ('AM Brothers 2027', True, ['Member', 'Pledge']))

        r = self.post(f'channels/{c.pk}/edit/', {'name': 'Class of 2027', 'audience': ['Member'], 'is_active': False})
        self.assertEqual(r.json()['detail'], '"Class of 2027" saved.')
        c.refresh_from_db()
        self.assertEqual((c.audience, c.is_active), (['Member'], False))

        self.assertEqual(self.post(f'channels/{c.pk}/move/', {'group_id': '333'}).json()['detail'],
                         'Pick a different group.')
        r = self.post(f'channels/{c.pk}/move/', {'group_id': '222'})
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.group_id, '222')
        self.assertTrue(any(x['path'] == '/bots/destroy' for x in self.fake.calls))

        self.assertEqual(self.post(f'channels/{c.pk}/test/').status_code, 200)
        self.assertEqual(self.post(f'channels/{c.pk}/test/').json()['detail'],
                         'Wait 30 seconds between test messages.')
        self.assertEqual(len(self.fake.posts()), 1)

        self.assertEqual(self.post(f'channels/{c.pk}/remove/').status_code, 200)
        self.assertFalse(GroupMeChannel.objects.filter(pk=c.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(description__startswith='GroupMe: removed').exists())

    def test_register_an_existing_bot_and_the_websites_checks(self):
        self.assertEqual(self.post('channels/', {'name': 'Nobody', 'bot_id': 'handmadeBot99'}).status_code, 400)
        self.assertEqual(self.post('channels/', {'name': 'x', 'audience': ['Member']}).json()['detail'],
                         'Connect a GroupMe account first, or paste a bot ID.')
        r = self.post('channels/', {'name': 'Hand made', 'bot_id': 'handmadeBot99', 'group_name': 'Old',
                                    'audience': 'Member'})
        self.assertEqual(r.status_code, 201)
        c = GroupMeChannel.objects.get(name='Hand made')
        self.assertEqual((c.bot_id, c.bot_created_here, c.group_name), ('handmadeBot99', False, 'Old'))
        self.assertEqual(self.post('channels/999999/edit/', {'name': 'x'}).status_code, 404)

    def test_log_and_retry(self):
        failed = GroupMeMessage.objects.create(channel=self.brothers, channel_name='Brothers', kind='announcement',
                                               text='hi', status='failed', error='boom', attempts=3)
        test = GroupMeMessage.objects.create(channel=self.brothers, channel_name='Brothers', kind='test',
                                             text='hi', status='failed')
        log = {m['id']: m for m in self.get().json()['log']}
        self.assertTrue(log[failed.pk]['can_retry'])
        self.assertFalse(log[test.pk]['can_retry'])
        self.assertEqual(log[test.pk]['what'], 'Test message')
        self.assertEqual(self.post(f'messages/{test.pk}/retry/').status_code, 404)   # tests aren't retried
        with mock.patch('src.view.officer.groupme.queue_send') as queue:
            r = self.post(f'messages/{failed.pk}/retry/')
        self.assertEqual(r.json()['detail'], 'Retrying. Refresh in a minute to see the result.')
        queue.assert_called_once_with([failed.pk])
