"""
Session revocation must go through the CONFIGURED session engine.

`UserSession.revoke_other_sessions` and `session_viewer.revoke_session`
deleted `django.contrib.sessions.models.Session` rows. Production runs
`SESSION_ENGINE='django.contrib.sessions.backends.cache'` (Redis), where that
table is empty — so "log out this device" / "log out everywhere else" removed
the display row and left the real session fully usable.

Each test is run against both the db and the cache (LocMem) engines and
checks the revoked session can no longer authenticate a request.
"""
from importlib import import_module

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.auth_backends import AUTH_BACKEND_PATH
from src.models import ParliamentUser, UserSession

DB_ENGINE = 'django.contrib.sessions.backends.db'
CACHE_ENGINE = 'django.contrib.sessions.backends.cache'
LOCMEM_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'session-revoke-engines',
    },
}


def make_user(uid='sessrevoke-user'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Session Revoke User', username=uid,
        member_type='Member', member_status='Active',
    )


def make_device_session(user):
    """A real, valid session for `user` in whatever engine is configured."""
    store = import_module(settings.SESSION_ENGINE).SessionStore()
    store[SESSION_KEY] = str(user.pk)
    store[BACKEND_SESSION_KEY] = AUTH_BACKEND_PATH
    store[HASH_SESSION_KEY] = user.get_session_auth_hash()
    store.save()
    UserSession.objects.create(
        user=user, session_key=store.session_key, ip_address='203.0.113.7',
        device_type='mobile', browser='Safari', operating_system='iOS',
    )
    return store.session_key


def client_with_session(session_key):
    client = Client()
    client.cookies[settings.SESSION_COOKIE_NAME] = session_key
    return client


class _RevokeMixin:
    def setUp(self):
        self.user = make_user()

    def assert_authenticates(self, session_key):
        resp = client_with_session(session_key).get(reverse('session_list'))
        self.assertEqual(resp.status_code, 200)

    def assert_logged_out(self, session_key):
        engine = import_module(settings.SESSION_ENGINE)
        self.assertFalse(engine.SessionStore().exists(session_key))
        resp = client_with_session(session_key).get(reverse('session_list'))
        self.assertEqual(resp.status_code, 302)

    def test_revoke_other_sessions_logs_other_device_out(self):
        keep = make_device_session(self.user)
        other = make_device_session(self.user)
        self.assert_authenticates(other)

        count = UserSession.revoke_other_sessions(self.user, keep_session_key=keep)

        self.assertEqual(count, 1)
        self.assert_logged_out(other)
        self.assertFalse(UserSession.objects.filter(session_key=other).exists())
        self.assert_authenticates(keep)

    def test_revoke_single_session_view_logs_device_out(self):
        other = make_device_session(self.user)
        self.assert_authenticates(other)

        client = Client()
        client.force_login(self.user)
        resp = client.post(reverse('revoke_session', args=[other]))

        self.assertEqual(resp.status_code, 200)
        self.assert_logged_out(other)
        self.assertFalse(UserSession.objects.filter(session_key=other).exists())


@override_settings(SESSION_ENGINE=DB_ENGINE)
class DbEngineSessionRevokeTests(_RevokeMixin, TestCase):
    pass


@override_settings(SESSION_ENGINE=CACHE_ENGINE, SESSION_CACHE_ALIAS='default', CACHES=LOCMEM_CACHES)
class CacheEngineSessionRevokeTests(_RevokeMixin, TestCase):
    pass
