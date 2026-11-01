"""
v4.4.0 — chat safety in the app (src/mobile/views_chat_safety.py): report a
message, mute a member, the moderators' review list, and the Chat tab hiding
what a member muted. Rules: src/chat_safety.py (web tests in
src/tests/chat/test_chat_safety.py).

Until its routes are in src/mobile/urls.py, this module is its own urlconf:
the project's urlconf plus the chat-safety routes.

    python manage.py test src.tests.mobile.test_chat_safety
"""
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path

from src import chat_safety
from src.mobile import views_chat_safety as v
from src.models import ChatChannel, ChatMessage, ChatMessageReport, ChatUserMute, Notification
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'

_routes = [
    path('chat/messages/<int:message_id>/report/', v.ReportMessageView.as_view(), name='mobile_chat_report'),
    path('chat/mutes/', v.MutesView.as_view(), name='mobile_chat_mutes'),
    path('chat/mutes/<str:user_id>/', v.UnmuteView.as_view(), name='mobile_chat_unmute'),
    path('chat/reports/', v.ReportListView.as_view(), name='mobile_chat_reports'),
    path('chat/reports/<int:report_id>/', v.ReportReviewView.as_view(), name='mobile_chat_report_review'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_cs')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.alice = make_user('mcs-alice')
        self.bob = make_user('mcs-bob')
        self.officer = make_user('mcs-officer', member_type='Officer')
        self.api = Client()
        self.tokens = {u.pk: make_device(u)[1] for u in (self.alice, self.bob, self.officer)}
        self.channel = ChatChannel.objects.create(name='General', access_type='open')

    def call(self, method, user, route, body=None, **params):
        kw = bearer(self.tokens[user.pk])
        if method == 'get':
            return self.api.get(API + route, params, **kw)
        return getattr(self.api, method)(API + route, body or {}, content_type='application/json', **kw)

    def msg(self, sender, text='hi'):
        return ChatMessage.objects.create(channel=self.channel, sender=sender, message=text)


class ReportApiTests(_Base):

    def test_report_then_moderator_reviews(self):
        bad = self.msg(self.bob, 'nasty')
        r = self.call('post', self.alice, f'chat/messages/{bad.id}/report/',
                      {'reason': 'hate', 'details': 'slur'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(Notification.objects.filter(recipient=self.officer, notification_type='chat_report').exists())

        r = self.call('post', self.alice, f'chat/messages/{bad.id}/report/', {'reason': 'hate'})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_reported'))

        reports = self.call('get', self.officer, 'chat/reports/').json()
        self.assertEqual(reports['open_count'], 1)
        row = reports['reports'][0]
        self.assertEqual((row['reporter']['id'], row['reason'], row['message']['text']),
                         (self.alice.user_id, 'hate', 'nasty'))

        r = self.call('post', self.officer, f'chat/reports/{row["id"]}/', {'action': 'delete_message'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['report']['status'], 'actioned')
        bad.refresh_from_db()
        self.assertTrue(bad.is_deleted)
        self.assertEqual(self.call('get', self.officer, 'chat/reports/').json()['open_count'], 0)

    def test_dismiss_and_bad_input(self):
        bad = self.msg(self.bob)
        r = self.call('post', self.alice, f'chat/messages/{bad.id}/report/', {'reason': 'nope'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.call('post', self.alice, f'chat/messages/{bad.id}/report/', {'reason': 'spam'})
        report = ChatMessageReport.objects.get()
        r = self.call('post', self.officer, f'chat/reports/{report.id}/', {'action': 'shrug'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', self.officer, f'chat/reports/{report.id}/', {'action': 'dismiss', 'note': 'fine'})
        self.assertEqual(r.json()['report']['status'], 'dismissed')
        r = self.call('get', self.officer, 'chat/reports/', status='bogus')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_non_moderators_and_reported_member_cannot_review(self):
        bad = self.msg(self.bob)
        self.call('post', self.alice, f'chat/messages/{bad.id}/report/', {'reason': 'spam'})
        report = ChatMessageReport.objects.get()
        for user in (self.alice, self.bob):
            self.assertEqual(self.call('get', user, 'chat/reports/').status_code, 403)
            self.assertEqual(self.call('post', user, f'chat/reports/{report.id}/',
                                       {'action': 'dismiss'}).status_code, 403)
        # The reported member never sees the reporter — nothing they can fetch names them.
        self.assertNotIn(self.alice.name.encode(), self.call('get', self.bob, 'chat/reports/').content)
        self.assertFalse(Notification.objects.filter(recipient=self.bob).exists())

    def test_unreadable_message_is_404(self):
        private = ChatChannel.objects.create(name='Officers', access_type='restricted')
        secret = ChatMessage.objects.create(channel=private, sender=self.bob, message='x')
        r = self.call('post', self.alice, f'chat/messages/{secret.id}/report/', {'reason': 'spam'})
        self.assertEqual(r.status_code, 404)

    def test_chats_flag_off(self):
        flag('chats', False)
        self.assertEqual(self.call('get', self.alice, 'chat/mutes/').status_code, 403)
        flag('chats', True)


class MuteApiTests(_Base):

    def test_mute_list_unmute(self):
        self.msg(self.bob, 'from bob')
        keep = self.msg(self.officer, 'from officer')
        r = self.call('post', self.alice, 'chat/mutes/', {'user_id': self.bob.user_id})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(self.call('post', self.alice, 'chat/mutes/', {'user_id': self.bob.user_id}).status_code, 200)
        mutes = self.call('get', self.alice, 'chat/mutes/').json()['mutes']
        self.assertEqual([m['user_id'] for m in mutes], [self.bob.user_id])

        # The Chat tab: message list, unread and last-message preview skip Bob.
        rows = self.call('get', self.alice, f'chat/channels/{self.channel.id}/messages/').json()['messages']
        self.assertEqual([m['message'] for m in rows], ['from officer'])
        channel = self.call('get', self.alice, 'chat/channels/').json()['channels'][0]
        self.assertEqual((channel['unread'], channel['last_message']['id']), (1, keep.id))
        # Bob and everyone else are unaffected.
        rows = self.call('get', self.officer, f'chat/channels/{self.channel.id}/messages/').json()['messages']
        self.assertEqual(len(rows), 2)

        r = self.call('delete', self.alice, f'chat/mutes/{self.bob.user_id}/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ChatUserMute.objects.exists())
        rows = self.call('get', self.alice, f'chat/channels/{self.channel.id}/messages/').json()['messages']
        self.assertEqual(len(rows), 2)

    def test_self_mute_and_bad_input(self):
        r = self.call('post', self.alice, 'chat/mutes/', {'user_id': self.alice.user_id})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'self_mute'))
        r = self.call('post', self.alice, 'chat/mutes/', {})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call('post', self.alice, 'chat/mutes/', {'user_id': 'P-NOBODY'})
        self.assertEqual(r.status_code, 404)

    def test_mute_stops_push_from_app_send(self):
        from datetime import timedelta
        from django.utils import timezone
        from src.models import ChatReadReceipt
        ChatReadReceipt.objects.create(user=self.alice, channel=self.channel)
        ChatReadReceipt.objects.create(user=self.officer, channel=self.channel)
        ChatReadReceipt.objects.update(last_read_at=timezone.now() - timedelta(minutes=5))
        chat_safety.mute(self.alice, self.bob)
        with mock.patch('src.tasks.send_push_notification') as push:
            r = self.call('post', self.bob, f'chat/channels/{self.channel.id}/send/', {'message': 'hey'})
        self.assertEqual(r.status_code, 200, r.content)
        pushed = {c.kwargs['user_id'] for c in push.delay.call_args_list}
        self.assertEqual(pushed, {self.officer.pk})


class ApiConfidentialityTests(_Base):

    def test_officer_outside_a_committee_channel_gets_nothing(self):
        from src.models import Committee
        chair = make_user('mcs-chair2')
        self.tokens[chair.pk] = make_device(chair)[1]
        kai = Committee.objects.create(name='Kai', code='MCSKAI', is_active=True)
        kai.members.add(self.alice, self.bob, chair)
        kai.chairs.add(chair)
        kai_chat = ChatChannel.objects.create(name='Kai', channel_type='committee', access_type='committee',
                                              committee=kai)
        secret = ChatMessage.objects.create(channel=kai_chat, sender=self.bob, message='kai secret')
        self.call('post', self.alice, f'chat/messages/{secret.id}/report/', {'reason': 'other'})
        report = ChatMessageReport.objects.get()

        body = self.call('get', self.officer, 'chat/reports/', status='all')
        self.assertEqual(body.json()['reports'], [])
        self.assertNotIn(b'kai secret', body.content)
        r = self.call('post', self.officer, f'chat/reports/{report.id}/', {'action': 'dismiss'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertEqual(self.call('get', self.officer, 'officer/').json()['open_chat_reports'], 0)

        rows = self.call('get', chair, 'chat/reports/').json()['reports']
        self.assertEqual([x['message']['text'] for x in rows], ['kai secret'])
        r = self.call('post', chair, f'chat/reports/{report.id}/', {'action': 'dismiss'})
        self.assertEqual(r.status_code, 200, r.content)


class OfficerHubFlagTests(_Base):

    def test_hub_says_who_moderates(self):
        bad = self.msg(self.bob)
        chat_safety.report_message(bad, self.alice, 'spam')
        hub = self.call('get', self.officer, 'officer/').json()
        self.assertEqual((hub['can_moderate_chat'], hub['open_chat_reports']), (True, 1))
        chair = make_user('mcs-chair', member_type='Chair')
        self.tokens[chair.pk] = make_device(chair)[1]
        hub = self.call('get', chair, 'officer/').json()
        self.assertEqual((hub['can_moderate_chat'], hub['open_chat_reports']), (False, None))
