"""
Mobile plan, Phase 1 — websocket tickets and the Origin check.

The app gets a 30 s single-use ticket from ``POST /api/mobile/v1/ws-ticket/``
and offers it as the subprotocol ``parliament.ticket.<ticket>`` next to plain
``parliament.ticket``, which the consumer echoes. Everything goes through the
real ``Parliament.asgi.application``.
"""
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ChatChannel
from src.models.mobile import MobileAuthTicket
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

ORIGIN = (b'origin', b'http://testserver')
HOST = (b'host', b'testserver')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class WsTicketTests(TransactionTestCase):

    def setUp(self):
        from Parliament.asgi import application
        self.application = application
        self.user = make_user('wst-1')
        # A 2FA user: the ticket must count as verified on its own.
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.device, self.access, _ = make_device(self.user, 'password_totp')
        self.channel = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open', created_by=self.user)

    def _ticket(self, access=None):
        r = Client().post('/api/mobile/v1/ws-ticket/', **bearer(access or self.access))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['ticket']

    def _connect(self, path=None, ticket=None, headers=(ORIGIN, HOST), cookie=None,
                 subprotocols=None):
        path = path or f'/ws/chat/{self.channel.pk}/'
        headers = list(headers)
        if cookie:
            headers.append((b'cookie', f'{settings.SESSION_COOKIE_NAME}={cookie}'.encode()))
        if subprotocols is None:
            subprotocols = ['parliament.ticket'] + ([f'parliament.ticket.{ticket}'] if ticket else [])

        async def run():
            comm = WebsocketCommunicator(self.application, path, headers=headers,
                                         subprotocols=subprotocols)
            connected, subprotocol = await comm.connect()
            await comm.disconnect()
            return connected, subprotocol
        return async_to_sync(run)()

    # ── the ticket ─────────────────────────────────────────────────────────
    def test_ticket_is_bound_to_the_device_and_lives_30s(self):
        self._ticket()
        t = MobileAuthTicket.objects.get(purpose=MobileAuthTicket.PURPOSE_WS)
        self.assertEqual(t.device_id, self.device.pk)
        self.assertLessEqual(t.expires_at - t.created_at, timedelta(seconds=31))

    def test_happy_path_connects_and_echoes_the_subprotocol(self):
        connected, subprotocol = self._connect(ticket=self._ticket())
        self.assertTrue(connected)
        self.assertEqual(subprotocol, 'parliament.ticket')
        self.assertTrue(self._connect('/ws/votes/', ticket=self._ticket())[0])

    def test_replay_is_refused(self):
        ticket = self._ticket()
        self.assertTrue(self._connect(ticket=ticket)[0])
        self.assertFalse(self._connect(ticket=ticket)[0])

    def test_expired_ticket_is_refused(self):
        ticket = self._ticket()
        MobileAuthTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(self._connect(ticket=ticket)[0])

    def test_revoked_device_ticket_is_refused(self):
        ticket = self._ticket()
        self.device.revoke('test')
        self.assertFalse(self._connect(ticket=ticket)[0])

    def test_ticket_in_the_query_string_is_ignored(self):
        ticket = self._ticket()
        connected, _ = self._connect(
            path=f'/ws/chat/{self.channel.pk}/?ticket={ticket}', subprotocols=[])
        self.assertFalse(connected)
        connected, _ = self._connect(
            path=f'/ws/chat/{self.channel.pk}/?parliament.ticket.{ticket}',
            subprotocols=['parliament.ticket'])
        self.assertFalse(connected)
        # Not consumed by either attempt.
        self.assertTrue(self._connect(ticket=ticket)[0])

    def test_a_handoff_code_is_not_a_ws_ticket(self):
        code = Client().post('/api/mobile/v1/auth/web-session/', **bearer(self.access)).json()['code']
        self.assertFalse(self._connect(ticket=code)[0])

    def test_garbage_ticket_is_refused(self):
        self.assertFalse(self._connect(ticket='nope')[0])

    def test_ticket_never_upgrades_an_unverified_session(self):
        # A password-only session for a 2FA user + a valid ticket: the session
        # user is present, so the ticket is ignored and the 2FA rule refuses.
        client = Client()
        client.force_login(self.user)
        connected, _ = self._connect(ticket=self._ticket(), cookie=client.session.session_key)
        self.assertFalse(connected)

    def test_access_rule_still_applies_to_ticket_users(self):
        private = ChatChannel.objects.create(
            name='Private', channel_type='custom', access_type='restricted',
            created_by=make_user('wst-owner'))
        connected, _ = self._connect(path=f'/ws/chat/{private.pk}/', ticket=self._ticket())
        self.assertFalse(connected)

    # ── Origin ─────────────────────────────────────────────────────────────
    def test_foreign_origin_is_refused_even_with_a_ticket(self):
        connected, _ = self._connect(
            ticket=self._ticket(), headers=[(b'origin', b'https://evil.example'), HOST])
        self.assertFalse(connected)

    def test_foreign_origin_is_refused_for_a_session(self):
        plain = make_user('wst-plain')
        client = Client()
        client.force_login(plain)
        key = client.session.session_key
        self.assertTrue(self._connect(cookie=key, subprotocols=[])[0])
        connected, _ = self._connect(
            cookie=key, subprotocols=[], headers=[(b'origin', b'https://evil.example'), HOST])
        self.assertFalse(connected)

    def test_missing_origin_is_allowed_only_for_a_ticket(self):
        self.assertTrue(self._connect(ticket=self._ticket(), headers=[HOST])[0])
        plain = make_user('wst-plain2')
        client = Client()
        client.force_login(plain)
        connected, _ = self._connect(
            cookie=client.session.session_key, subprotocols=[], headers=[HOST])
        self.assertFalse(connected)
