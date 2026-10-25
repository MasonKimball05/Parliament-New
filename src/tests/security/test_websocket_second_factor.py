"""
v3.51.9 — websockets honour the 2FA verify step.

`Enforce2FAMiddleware` is HTTP middleware. A session that has passed the
password check but not the TOTP step is *authenticated* (Django has logged it
in; django-otp only adds `is_verified()`), and every HTTP page bounces it to
`/accounts/two-factor/verify/`. The websocket stack never ran that check: all
four consumers ask only `user.is_authenticated`. So a password-only session
for an account with 2FA turned on could open `/ws/chat/<id>/` and receive
every new chat message as it was sent, the `/ws/votes/` pings, and the
resolution sockets (draft text and presence) — the same class of hole as the
07-22 sweep's Finding A (the `/api/` exemption), on a different transport.

`SecondFactorWebsocketMiddleware` (asgi.py, inside `AuthMiddlewareStack`)
turns such a session into `AnonymousUser` for the socket, which every consumer
already refuses. These tests go through the real `Parliament.asgi.application`
with a real session cookie, so they exercise the same stack prod runs.
"""
from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import Client, TransactionTestCase, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.impersonation import SESSION_ORIGINAL_ID
from src.models import ChatChannel, ParliamentUser


def make_user(uid, is_admin=False):
    return ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type='Member', member_status='Active', is_admin=is_admin,
    )


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class WebsocketSecondFactorTests(TransactionTestCase):

    def setUp(self):
        from Parliament.asgi import application
        self.application = application
        self.user = make_user('ws2fa-1')
        self.device = TOTPDevice.objects.create(
            user=self.user, name='phone', confirmed=True)
        self.channel = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open',
            created_by=self.user)

    # ── helpers ──────────────────────────────────────────────────────────
    def _session_key(self, user, **session_values):
        client = Client()
        client.force_login(user)
        session = client.session
        for key, value in session_values.items():
            session[key] = value
        session.save()
        return session.session_key

    def _connects(self, path, session_key):
        cookie = f'{settings.SESSION_COOKIE_NAME}={session_key}'.encode()

        async def run():
            comm = WebsocketCommunicator(
                self.application, path,
                headers=[(b'cookie', cookie), (b'origin', b'http://testserver'),
                         (b'host', b'testserver')])
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        return async_to_sync(run)()

    def chat(self):
        return f'/ws/chat/{self.channel.pk}/'

    # ── the hole ─────────────────────────────────────────────────────────
    def test_password_only_session_cannot_open_the_chat_socket(self):
        key = self._session_key(self.user)
        self.assertFalse(self._connects(self.chat(), key))

    def test_password_only_session_cannot_open_the_votes_socket(self):
        key = self._session_key(self.user)
        self.assertFalse(self._connects('/ws/votes/', key))

    def test_an_unconfirmed_device_does_not_count(self):
        # HTTP's user_has_device() ignores unconfirmed devices (a half-done
        # setup), so the socket must not lock such a user out either.
        TOTPDevice.objects.filter(pk=self.device.pk).update(confirmed=False)
        key = self._session_key(self.user)
        self.assertTrue(self._connects(self.chat(), key))

    def test_another_users_device_id_in_the_session_does_not_verify(self):
        other = make_user('ws2fa-2')
        other_device = TOTPDevice.objects.create(user=other, name='x', confirmed=True)
        key = self._session_key(self.user, otp_device_id=other_device.persistent_id)
        self.assertFalse(self._connects(self.chat(), key))

    # ── what must keep working ───────────────────────────────────────────
    def test_verified_session_connects(self):
        key = self._session_key(self.user, otp_device_id=self.device.persistent_id)
        self.assertTrue(self._connects(self.chat(), key))
        self.assertTrue(self._connects('/ws/votes/', key))

    def test_user_without_2fa_connects(self):
        plain = make_user('ws2fa-3')
        key = self._session_key(plain)
        self.assertTrue(self._connects(self.chat(), key))

    def test_passkey_login_counts_as_verified(self):
        key = self._session_key(self.user, webauthn_authenticated=True)
        self.assertTrue(self._connects(self.chat(), key))

    def test_impersonation_session_connects(self):
        # Same bypass as Enforce2FAMiddleware: the admin doesn't hold the
        # member's authenticator (see src/impersonation.py).
        admin = make_user('ws2fa-admin', is_admin=True)
        key = self._session_key(self.user, **{SESSION_ORIGINAL_ID: admin.pk})
        self.assertTrue(self._connects(self.chat(), key))

    def test_no_session_is_still_refused(self):
        self.assertFalse(self._connects(self.chat(), 'nope'))


class SecondFactorHelperParityTests(TransactionTestCase):
    """The socket rule and the HTTP rule are the same function's inputs."""

    def test_helper_matches_http_device_rule(self):
        from src.middleware.two_factor import session_passes_second_factor
        user = make_user('ws2fa-p')
        self.assertTrue(session_passes_second_factor(user, {}))
        device = TOTPDevice.objects.create(user=user, name='p', confirmed=True)
        self.assertFalse(session_passes_second_factor(user, {}))
        self.assertTrue(session_passes_second_factor(
            user, {'otp_device_id': device.persistent_id}))
        self.assertFalse(session_passes_second_factor(
            user, {'otp_device_id': 'otp_totp.totpdevice/999999'}))
        self.assertFalse(session_passes_second_factor(
            user, {'otp_device_id': 'garbage'}))
