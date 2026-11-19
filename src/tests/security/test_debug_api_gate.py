"""
v4.4.15 (11-19-26 pentest, attack-surface item 1) — the /api/debug/ endpoints need
the full admin-v2 gate, and the session editor can't touch auth keys.

Before: any `is_admin` could run them (raw SQL info, flag toggles, cache/log
clears, and a session editor that could write `admin_v2_authenticated` or the
impersonation key).
"""
import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.impersonation import SESSION_ORIGINAL_ID
from src.models import ParliamentUser

URL = '/api/debug/session/'
EDIT = '/api/debug/session/edit/'


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class DebugApiGateTests(TestCase):
    def setUp(self):
        mk = ParliamentUser.objects.create_user
        self.admin = mk('dbg-adm', 'Plain Admin', 'dbgadm', 'Officer', 'x', is_admin=True)
        self.dev = mk('dbg-dev', 'Dev Admin', 'dbgdev', 'Officer', 'x', is_admin=True)
        self.member = mk('dbg-mem', 'Member', 'dbgmem', 'Member', 'x')
        patcher = mock.patch('src.view.admin_v2.ALLOWED_USER_IDS', {'dbg-dev'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, user, stepped_up=False, when=None):
        c = Client()
        c.force_login(user)
        if stepped_up:
            s = c.session
            s['admin_v2_authenticated'] = True
            s['admin_v2_auth_time'] = (when or timezone.now()).isoformat()
            s.save()
        return c

    def test_a_plain_admin_gets_a_404(self):
        self.assertEqual(self._client(self.admin).get(URL).status_code, 404)

    def test_a_member_gets_a_404(self):
        self.assertEqual(self._client(self.member).get(URL).status_code, 404)

    def test_the_allowlist_alone_is_not_enough(self):
        self.assertEqual(self._client(self.dev).get(URL).status_code, 404)

    def test_an_expired_admin_v2_sign_in_is_not_enough(self):
        c = self._client(self.dev, stepped_up=True, when=timezone.now() - timedelta(days=8))
        self.assertEqual(c.get(URL).status_code, 404)

    def test_allowlist_plus_a_live_admin_v2_sign_in_works(self):
        self.assertEqual(self._client(self.dev, stepped_up=True).get(URL).status_code, 200)

    def test_the_session_editor_refuses_auth_keys(self):
        c = self._client(self.dev, stepped_up=True)
        for key in ('admin_v2_authenticated', '_auth_user_id', SESSION_ORIGINAL_ID,
                    'otp_device_id', 'webauthn_reauth_grant_at', 'login_geo_suspicious'):
            for action in ('set', 'delete'):
                r = c.post(EDIT, json.dumps({'action': action, 'key': key, 'value': 'x'}),
                           content_type='application/json')
                self.assertEqual(r.status_code, 403, f'{action} {key}')
        r = c.post(EDIT, json.dumps({'action': 'set', 'key': 'debug_note', 'value': 'ok'}),
                   content_type='application/json')
        self.assertEqual(r.status_code, 200)
