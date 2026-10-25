"""
The chat WebSocket used `can_read`, which disagreed with the channel page's
`has_access`: member-type / role grants were refused a socket the page let
them open, and committee members were admitted to committee-linked channels
the page refused. It now uses the page's own gate (read rule + admin override).
"""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from src.models import ChatChannel, ChatChannelPermission, Committee, ParliamentUser


def make_user(uid, **extra):
    fields = dict(user_id=uid, name=f'User {uid}', username=uid,
                  member_type='Member', member_status='Active')
    fields.update(extra)
    return ParliamentUser.objects.create(**fields)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ChatSocketAccessTests(TransactionTestCase):

    def setUp(self):
        from Parliament.asgi import application
        self.application = application
        self.pledge = make_user('cs-pledge', member_type='Pledge')
        self.admin = make_user('cs-admin', is_admin=True)
        self.cmember = make_user('cs-cmember')
        self.committee = Committee.objects.create(name='Kai', code='CSK')
        self.committee.members.add(self.cmember)

    def _connects(self, user, channel):
        client = Client()
        client.force_login(user)
        cookie = f'{settings.SESSION_COOKIE_NAME}={client.session.session_key}'.encode()

        async def run():
            comm = WebsocketCommunicator(
                self.application, f'/ws/chat/{channel.pk}/',
                headers=[(b'cookie', cookie), (b'origin', b'http://testserver'),
                         (b'host', b'testserver')])
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        return async_to_sync(run)()

    def test_member_type_grant_matches_page(self):
        channel = ChatChannel.objects.create(
            name='Pledges', channel_type='custom', access_type='restricted')
        perm = ChatChannelPermission.objects.create(channel=channel, member_type='Pledge')
        self.assertTrue(self._connects(self.pledge, channel))
        perm.expires_at = timezone.now() - timedelta(hours=1)
        perm.save()
        self.assertFalse(self._connects(self.pledge, channel))

    def test_committee_membership_does_not_open_a_restricted_channel(self):
        channel = ChatChannel.objects.create(
            name='Kai side', channel_type='custom', access_type='restricted',
            committee=self.committee)
        self.assertFalse(self._connects(self.cmember, channel))

    def test_admin_preview_still_gets_live_updates(self):
        channel = ChatChannel.objects.create(
            name='Kai', channel_type='committee', access_type='committee',
            committee=self.committee)
        self.assertTrue(self._connects(self.admin, channel))
        self.assertTrue(self._connects(self.cmember, channel))
        self.assertFalse(self._connects(self.pledge, channel))
