"""
Mobile plan, Phase 3 — the Chat endpoints (v4.0.0), the shared chat service
the web chat now uses (src/chat_service.py), the one-query unread_map, and
app (ticket) sockets losing the admin "View All" override.

    python manage.py test src.tests.mobile.test_chat
"""
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ChatChannel, ChatChannelPermission, ChatMessage, ChatReadReceipt, Committee, Notification,
)
from src.models.chat import ChatNotificationPreference
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('chat-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()
        self.owner = make_user('chat-owner')
        self.open = ChatChannel.objects.create(name='General', access_type='open',
                                               created_by=self.owner)
        self.private = ChatChannel.objects.create(name='Officers', access_type='restricted',
                                                  created_by=self.owner)
        self.kai = Committee.objects.create(name='Kai', code='KAI', is_active=True)
        self.kai_chat = ChatChannel.objects.create(name='Kai', channel_type='committee',
                                                   access_type='committee', committee=self.kai,
                                                   created_by=self.owner)

    def get(self, path, access=None, **params):
        return self.api.get(API + path, params, **bearer(access or self.access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, body or {}, content_type='application/json',
                             **bearer(access or self.access))

    def msg(self, channel, sender, text='hi', **kw):
        return ChatMessage.objects.create(channel=channel, sender=sender, message=text, **kw)


class ChannelListTests(_Base):

    def test_only_readable_channels_with_unread_and_last_message(self):
        self.msg(self.open, self.owner, 'first')
        last = self.msg(self.open, self.owner, 'second')
        self.msg(self.private, self.owner, 'secret')
        self.msg(self.kai_chat, self.owner, 'kai secret')
        channels = self.get('chat/channels/').json()['channels']
        self.assertEqual([c['name'] for c in channels], ['General'])
        general = channels[0]
        self.assertEqual((general['unread'], general['last_message']['id']), (2, last.id))
        self.assertTrue(general['can_write'])
        self.assertEqual(general['notification_level'], 'all')

    def test_admin_gets_no_view_all(self):
        admin = make_user('chat-admin', is_admin=True)
        _, access, _ = make_device(admin)
        names = [c['name'] for c in self.get('chat/channels/', access=access).json()['channels']]
        self.assertEqual(names, ['General'])

    def test_grants_and_read_only(self):
        ChatChannelPermission.objects.create(channel=self.private, user=self.user,
                                             can_read=True, can_write=False)
        self.kai.members.add(self.user)
        self.open.is_read_only = True
        self.open.save()
        by_name = {c['name']: c for c in self.get('chat/channels/').json()['channels']}
        self.assertEqual(set(by_name), {'General', 'Officers', 'Kai'})
        self.assertFalse(by_name['Officers']['can_write'])
        self.assertTrue(by_name['Kai']['can_write'])
        self.assertFalse(by_name['General']['can_write'])

    def test_expired_grant_is_gone(self):
        ChatChannelPermission.objects.create(channel=self.private, user=self.user, can_read=True,
                                             expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual([c['name'] for c in self.get('chat/channels/').json()['channels']],
                         ['General'])

    def test_flag_toggle_and_pledge_gates(self):
        flag('chats', False)
        r = self.get('chat/channels/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        flag('chats', True)
        toggle('chat_index', False)
        self.assertEqual(self.get('chat/channels/').json()['error'], 'page_disabled')
        toggle('chat_index', True)
        pledge = make_user('P-CHAT01', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('chat_index')
        self.assertEqual(self.get('chat/channels/', access=access).json()['error'],
                         'pledge_restricted')

    def test_list_queries_do_not_grow_with_channels(self):
        def count():
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                self.get('chat/channels/')
            return len(ctx.captured_queries)
        def add(n):
            for i in range(n):
                c = ChatChannel.objects.create(name=f'C{n}-{i}', access_type='restricted',
                                               created_by=self.owner)
                ChatChannelPermission.objects.create(channel=c, user=self.user, can_read=True)
                self.msg(c, self.owner)
        add(8)
        eight = count()
        add(8)
        self.assertEqual(count(), eight)


class MessageTests(_Base):

    def test_paging_by_id(self):
        msgs = [self.msg(self.open, self.owner, f'm{i}') for i in range(5)]
        page = self.get(f'chat/channels/{self.open.id}/messages/', limit=2).json()
        self.assertEqual([m['message'] for m in page['messages']], ['m3', 'm4'])
        self.assertTrue(page['has_more'])
        older = self.get(f'chat/channels/{self.open.id}/messages/', limit=2,
                         before=page['messages'][0]['id']).json()
        self.assertEqual([m['message'] for m in older['messages']], ['m1', 'm2'])
        newer = self.get(f'chat/channels/{self.open.id}/messages/', since=msgs[2].id).json()
        self.assertEqual([m['message'] for m in newer['messages']], ['m3', 'm4'])

    def test_unreadable_channel_is_404(self):
        self.msg(self.kai_chat, self.owner, 'kai secret')
        for path in (f'chat/channels/{self.kai_chat.id}/messages/',
                     f'chat/channels/{self.private.id}/notification-level/'):
            self.assertEqual(self.get(path).status_code, 404, path)
        r = self.post(f'chat/channels/{self.kai_chat.id}/send/', {'message': 'x'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(ChatMessage.objects.filter(channel=self.kai_chat).count(), 1)

    def test_fetching_marks_presence(self):
        self.get(f'chat/channels/{self.open.id}/messages/')
        receipt = ChatReadReceipt.objects.get(user=self.user, channel=self.open)
        self.assertGreater(receipt.last_read_at, timezone.now() - timedelta(seconds=5))

    def test_edit_and_delete_flags(self):
        mine = self.msg(self.open, self.user, 'mine')
        old = self.msg(self.open, self.user, 'old')
        ChatMessage.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=2))
        theirs = self.msg(self.open, self.owner, 'theirs')
        rows = {m['id']: m for m in self.get(f'chat/channels/{self.open.id}/messages/').json()['messages']}
        self.assertEqual((rows[mine.id]['can_edit'], rows[mine.id]['can_delete']), (True, True))
        self.assertEqual((rows[old.id]['can_edit'], rows[old.id]['can_delete']), (False, True))
        self.assertEqual((rows[theirs.id]['can_edit'], rows[theirs.id]['can_delete']), (False, False))


class SendEditDeleteTests(_Base):

    def test_send_then_edit_then_delete(self):
        r = self.post(f'chat/channels/{self.open.id}/send/', {'message': '  hello  '})
        self.assertEqual(r.status_code, 200, r.content)
        mid = r.json()['message']['id']
        self.assertEqual(r.json()['message']['message'], 'hello')
        r = self.post(f'chat/channels/{self.open.id}/messages/{mid}/edit/', {'message': 'hello!'})
        self.assertEqual(r.json()['message']['message'], 'hello!')
        self.assertIsNotNone(r.json()['message']['edited_at'])
        self.post(f'chat/channels/{self.open.id}/messages/{mid}/delete/')
        self.assertTrue(ChatMessage.objects.get(pk=mid).is_deleted)

    def test_refusals(self):
        for body, code in (({'message': ''}, 'empty'), ({'message': 'x' * 2001}, 'too_long'), ({}, 'empty')):
            r = self.post(f'chat/channels/{self.open.id}/send/', body)
            self.assertEqual((r.status_code, r.json()['error']), (400, code), body)
        theirs = self.msg(self.open, self.owner, 'theirs')
        r = self.post(f'chat/channels/{self.open.id}/messages/{theirs.id}/edit/', {'message': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        r = self.post(f'chat/channels/{self.open.id}/messages/{theirs.id}/delete/')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(ChatMessage.objects.get(pk=theirs.pk).is_deleted)

    def test_cannot_write_to_a_read_only_grant(self):
        ChatChannelPermission.objects.create(channel=self.private, user=self.user, can_read=True,
                                             can_write=False)
        r = self.post(f'chat/channels/{self.private.id}/send/', {'message': 'x'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))

    def test_rate_limit_is_shared_with_the_web(self):
        web = Client()
        web.force_login(self.user)
        for i in range(5):
            web.post(reverse('send_channel_message', kwargs={'channel_id': self.open.id}),
                     {'message': f'w{i}'})
        r = self.post(f'chat/channels/{self.open.id}/send/', {'message': 'one more'})
        self.assertEqual((r.status_code, r.json()['error']), (429, 'rate_limited'))

    def test_message_from_another_channel_is_404(self):
        other = self.msg(self.kai_chat, self.owner, 'kai')
        r = self.post(f'chat/channels/{self.open.id}/messages/{other.id}/delete/')
        self.assertEqual(r.status_code, 404)

    def test_mention_reaches_only_readers_and_private_push_text_is_hidden(self):
        self.kai.members.add(self.user)
        # Mentions match [A-Za-z0-9._]: no hyphens in these usernames.
        outsider = make_user('chatoutsider')
        insider = make_user('chatinsider')
        self.kai.members.add(insider)
        ChatReadReceipt.objects.create(user=insider, channel=self.kai_chat)
        ChatReadReceipt.objects.filter(user=insider).update(
            last_read_at=timezone.now() - timedelta(minutes=5))
        with patch('src.tasks.send_push_notification') as push:
            self.post(f'chat/channels/{self.kai_chat.id}/send/',
                      {'message': '@chatoutsider @chatinsider secret plan'})
        self.assertFalse(Notification.objects.filter(recipient=outsider).exists())
        self.assertTrue(Notification.objects.filter(recipient=insider,
                                                     notification_type='chat_mention').exists())
        bodies = [c.kwargs['body'] for c in push.delay.call_args_list]
        self.assertTrue(bodies)
        self.assertFalse(any('secret' in b for b in bodies), bodies)

    def test_web_endpoints_still_work(self):
        web = Client()
        web.force_login(self.user)
        r = web.post(reverse('send_channel_message', kwargs={'channel_id': self.open.id}),
                     {'message': 'from web'})
        self.assertTrue(r.json()['success'])
        mid = r.json()['message']['id']
        r = web.post(reverse('edit_channel_message', kwargs={'channel_id': self.open.id,
                                                               'message_id': mid}),
                     {'message': 'edited'})
        self.assertEqual(r.json()['message']['message'], 'edited')
        r = web.post(reverse('delete_channel_message', kwargs={'channel_id': self.open.id,
                                                                 'message_id': mid}))
        self.assertTrue(r.json()['success'])
        r = web.post(reverse('send_channel_message', kwargs={'channel_id': self.private.id}),
                     {'message': 'nope'})
        self.assertEqual(r.status_code, 403)


class ReadAndLevelTests(_Base):

    def test_read_marker_and_unread(self):
        a = self.msg(self.open, self.owner, 'a')
        self.msg(self.open, self.owner, 'b')
        r = self.post(f'chat/channels/{self.open.id}/read/', {'up_to': a.id})
        self.assertEqual(r.json()['last_read_message_id'], a.id)
        self.assertEqual(self.get('chat/channels/').json()['channels'][0]['unread'], 1)
        self.post(f'chat/channels/{self.open.id}/read/')
        self.assertEqual(self.get('chat/channels/').json()['channels'][0]['unread'], 0)

    def test_notification_level(self):
        path = f'chat/channels/{self.open.id}/notification-level/'
        self.assertEqual(self.get(path).json(), {'level': 'all'})
        self.assertEqual(self.post(path, {'level': 'mentions'}).json(), {'level': 'mentions'})
        self.assertEqual(ChatNotificationPreference.objects.get(user=self.user).level, 'mentions')
        self.assertEqual(self.post(path, {'level': 'loud'}).json()['error'], 'invalid_level')


class UnreadMapTests(TestCase):
    """The one-query unread_map agrees with get_unread_count everywhere."""

    def test_matches_per_channel_count(self):
        owner, user = make_user('um-owner'), make_user('um-user')
        no_receipt, no_marker, marked = (
            ChatChannel.objects.create(name=n, access_type='open', created_by=owner)
            for n in ('a', 'b', 'c'))
        for c in (no_receipt, no_marker, marked):
            for i in range(3):
                ChatMessage.objects.create(channel=c, sender=owner, message=str(i))
            ChatMessage.objects.create(channel=c, sender=owner, message='gone', is_deleted=True)
        ChatReadReceipt.objects.create(user=user, channel=no_marker)
        first = ChatMessage.objects.filter(channel=marked).order_by('created_at').first()
        ChatReadReceipt.objects.create(user=user, channel=marked, last_read_message=first)
        empty = ChatChannel.objects.create(name='d', access_type='open', created_by=owner)
        channels = [no_receipt, no_marker, marked, empty]
        expected = {c.pk: c.get_unread_count(user) for c in channels}
        self.assertEqual(ChatChannel.unread_map(channels, user), expected)
        self.assertEqual(expected, {no_receipt.pk: 3, no_marker.pk: 3, marked.pk: 2, empty.pk: 0})


ORIGIN = (b'origin', b'http://testserver')
HOST = (b'host', b'testserver')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AppSocketHasNoViewAllTests(TransactionTestCase):
    """v4.0.0 — an admin's app socket can't tail a channel they aren't in."""

    def setUp(self):
        from Parliament.asgi import application
        self.application = application
        self.admin = make_user('ws-admin', is_admin=True)
        _, self.access, _ = make_device(self.admin)
        owner = make_user('ws-owner')
        committee = Committee.objects.create(name='Kai', code='WSKAI', is_active=True)
        self.kai_chat = ChatChannel.objects.create(name='Kai', channel_type='committee',
                                                   access_type='committee', committee=committee,
                                                   created_by=owner)
        self.open = ChatChannel.objects.create(name='General', access_type='open', created_by=owner)

    def _connect(self, channel):
        ticket = Client().post(API + 'ws-ticket/', **bearer(self.access)).json()['ticket']

        async def run():
            comm = WebsocketCommunicator(
                self.application, f'/ws/chat/{channel.pk}/', headers=[ORIGIN, HOST],
                subprotocols=['parliament.ticket', f'parliament.ticket.{ticket}'])
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        return async_to_sync(run)()

    def test_admin_ticket_socket_needs_real_access(self):
        self.assertTrue(self._connect(self.open))
        self.assertFalse(self._connect(self.kai_chat))

    def test_chats_flag_off_refuses_app_sockets(self):
        flag('chats', False)
        self.assertFalse(self._connect(self.open))
