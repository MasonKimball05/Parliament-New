"""
The chat send path: @mentions, push fan-out, and the per-channel
notification preference endpoint.

* `_parse_mentions` matched any active username in the chapter, so a
  `chat_mention` Notification carrying `message_text[:200]` went to people who
  cannot read the channel (e.g. naming a non-member in Kai's chat).
* `_dispatch_chat_push` sent the channel name and message text to every user
  with a read receipt — including users whose access has since lapsed — and
  put private-channel text on lock screens.
* `set_channel_notification_pref` saved and returned None (a 500).
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ChatChannel, ChatChannelPermission, ChatNotificationPreference,
    ChatReadReceipt, Committee, Notification, ParliamentUser,
)


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid.replace('-', '_'),
                  member_type='Member', member_status='Active')
    fields.update(extra)
    return ParliamentUser.objects.create(**fields)


class MentionScopeTests(TestCase):
    def setUp(self):
        self.sender = make_user('ms-sender')
        self.member = make_user('ms-member')
        self.outsider = make_user('ms-outsider')
        committee = Committee.objects.create(name='Kai', code='MSK')
        committee.members.add(self.sender, self.member)
        self.channel = ChatChannel.objects.create(
            name='Kai', channel_type='committee', access_type='committee',
            committee=committee)
        self.client = Client()
        self.client.force_login(self.sender)

    def send(self, text):
        with mock.patch('src.tasks.send_push_notification'):
            return self.client.post(
                reverse('send_channel_message', args=[self.channel.id]),
                {'message': text})

    def test_outsider_named_in_private_channel_gets_nothing(self):
        response = self.send('@ms_outsider @ms_member confidential case detail')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Notification.objects.filter(
            recipient=self.outsider, notification_type='chat_mention').exists())
        self.assertTrue(Notification.objects.filter(
            recipient=self.member, notification_type='chat_mention').exists())

    def test_parse_mentions_is_scoped_to_readers(self):
        from src.view.chat.channel_chat import _parse_mentions
        found = _parse_mentions('@ms_outsider @ms_member hi', self.channel)
        self.assertEqual(found, {self.member.pk})

    def test_expired_guest_is_not_mentionable(self):
        ChatChannelPermission.objects.create(
            channel=self.channel, user=self.outsider, can_read=True,
            expires_at=timezone.now() - timedelta(hours=1))
        from src.view.chat.channel_chat import _parse_mentions
        self.assertEqual(_parse_mentions('@ms_outsider', self.channel), set())

    def test_open_channel_mentions_anyone_active(self):
        open_ch = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open')
        from src.view.chat.channel_chat import _parse_mentions
        self.assertEqual(_parse_mentions('@ms_outsider', open_ch),
                         {self.outsider.pk})


class PushFanoutTests(TestCase):
    def setUp(self):
        self.sender = make_user('pf-sender')
        self.reader = make_user('pf-reader')
        self.lapsed = make_user('pf-lapsed')
        self.client = Client()
        self.client.force_login(self.sender)

    def _receipts(self, channel, *users):
        for u in users:
            ChatReadReceipt.objects.create(user=u, channel=channel)
        ChatReadReceipt.objects.filter(channel=channel).update(
            last_read_at=timezone.now() - timedelta(minutes=5))

    def _send(self, channel, text):
        with mock.patch('src.tasks.send_push_notification') as task:
            response = self.client.post(
                reverse('send_channel_message', args=[channel.id]),
                {'message': text})
        self.assertEqual(response.status_code, 200)
        return [c.kwargs for c in task.delay.call_args_list]

    def test_open_channel_push_keeps_text(self):
        channel = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open')
        self._receipts(channel, self.reader)
        calls = self._send(channel, 'pizza at 7')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['title'], '#General')
        self.assertIn('pizza at 7', calls[0]['body'])

    def test_committee_channel_push_hides_name_and_text(self):
        committee = Committee.objects.create(name='Kai', code='PFK')
        committee.members.add(self.sender, self.reader)
        channel = ChatChannel.objects.create(
            name='Kai', channel_type='committee', access_type='committee',
            committee=committee)
        self._receipts(channel, self.reader)
        calls = self._send(channel, 'case 12 hearing')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['title'], 'Parliament')
        self.assertEqual(calls[0]['body'], 'New message in a private channel')
        self.assertNotIn('Kai', json.dumps(calls[0]))

    def test_restricted_channel_push_hides_text_and_skips_lapsed(self):
        channel = ChatChannel.objects.create(
            name='Exec', channel_type='custom', access_type='restricted')
        for u in (self.sender, self.reader):
            ChatChannelPermission.objects.create(channel=channel, user=u)
        ChatChannelPermission.objects.create(
            channel=channel, user=self.lapsed,
            expires_at=timezone.now() - timedelta(hours=1))
        self._receipts(channel, self.reader, self.lapsed)
        calls = self._send(channel, 'budget numbers')
        self.assertEqual([c['user_id'] for c in calls], [self.reader.pk])
        self.assertEqual(calls[0]['title'], 'Parliament')
        self.assertEqual(calls[0]['body'], 'New message in a private channel')


class NotificationPrefTests(TestCase):
    def test_set_pref_returns_json(self):
        user = make_user('np-user')
        channel = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open')
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse('set_channel_notification_pref', args=[channel.id]),
            data=json.dumps({'level': 'mentions'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'success': True, 'level': 'mentions'})
        self.assertEqual(ChatNotificationPreference.objects.get(
            user=user, channel=channel).level, 'mentions')
