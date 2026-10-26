"""
Mobile plan, Phase 1 — the web-view session handoff and the session guard.

``POST /api/mobile/v1/auth/web-session/`` mints a 60 s single-use code bound to
the calling device; ``POST /m/handoff/`` (code + the SAME device's bearer)
turns it into a site session that passes the 2FA middleware the same way a
website sign-in does. ``MobileSessionGuardMiddleware`` then ends that session
as soon as the device stops being allowed.
"""
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.middleware.two_factor import session_passes_second_factor
from src.models import ActivityLog, LoginHistory, UserSession
from src.models.mobile import MobileAuthTicket
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

PROTECTED = '/account/sessions/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class HandoffTests(TestCase):

    def setUp(self):
        self.user = make_user('ho-1')
        self.totp = TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.device, self.access, _ = make_device(self.user, 'password_totp')
        self.api = Client()
        self.web = Client()

    def _code(self, access=None):
        r = self.api.post('/api/mobile/v1/auth/web-session/', **bearer(access or self.access))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r['Cache-Control'], 'no-store')
        return r.json()['code']

    def _handoff(self, code, access=None, next_url='/account/sessions/', client=None):
        headers = bearer(access or self.access) if (access or self.access) else {}
        return (client or self.web).post('/m/handoff/', {'code': code, 'next': next_url}, **headers)

    # ── the code ───────────────────────────────────────────────────────────
    def test_code_is_bound_to_the_device_and_short_lived(self):
        code = self._code()
        ticket = MobileAuthTicket.objects.get(purpose=MobileAuthTicket.PURPOSE_WEB_HANDOFF)
        self.assertEqual(ticket.device_id, self.device.pk)
        self.assertLessEqual(ticket.expires_at - ticket.created_at, timedelta(seconds=61))
        self.assertNotEqual(ticket.token_hash, code)

    def test_a_session_cookie_cannot_mint_a_code(self):
        # A fully verified website session, so only the auth class decides.
        self.api.force_login(self.user)
        session = self.api.session
        session['otp_device_id'] = self.totp.persistent_id
        session.save()
        r = self.api.post('/api/mobile/v1/auth/web-session/')
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(MobileAuthTicket.objects.exists())

    # ── happy path ─────────────────────────────────────────────────────────
    def test_handoff_signs_in_and_passes_the_2fa_middleware(self):
        r = self._handoff(self._code())
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r['Location'], '/account/sessions/')
        self.assertEqual(r['Referrer-Policy'], 'no-referrer')

        session = self.web.session
        self.assertEqual(session['mobile_device_id'], self.device.pk)
        self.assertTrue(session_passes_second_factor(self.user, session))
        page = self.web.get(PROTECTED)
        self.assertEqual(page.status_code, 200)      # not the verify step
        self.assertTrue(UserSession.objects.filter(
            user=self.user, session_key=session.session_key).exists())

    def test_passkey_device_marks_the_session_like_a_passkey_login(self):
        device, access, _ = make_device(self.user, 'passkey')
        self._handoff(self._code(access), access=access)
        self.assertTrue(self.web.session.get('webauthn_authenticated'))
        self.assertEqual(self.web.get(PROTECTED).status_code, 200)

    def test_password_only_device_for_a_member_who_since_enabled_2fa_is_signed_out(self):
        # Review finding 1: still_allowed() fails (second_factor_required), so
        # the device can neither mint a code nor hand off.
        device, access, _ = make_device(self.user, 'password')
        r = self.api.post('/api/mobile/v1/auth/web-session/', **bearer(access))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self._handoff('anything', access=access).status_code, 403)
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_unsafe_next_falls_back_to_home(self):
        r = self._handoff(self._code(), next_url='https://evil.example/')
        self.assertEqual(r['Location'], '/')
        r = self._handoff(self._code(), next_url='//evil.example/', client=Client())
        self.assertEqual(r['Location'], '/')

    def test_handoff_does_not_record_a_second_sign_in(self):
        before_history = LoginHistory.objects.filter(user=self.user).count()
        before_activity = ActivityLog.objects.filter(user=self.user, action_type='login').count()
        self._handoff(self._code())
        self.assertEqual(LoginHistory.objects.filter(user=self.user).count(), before_history)
        self.assertEqual(
            ActivityLog.objects.filter(user=self.user, action_type='login').count(), before_activity)

    # ── refusals ───────────────────────────────────────────────────────────
    def test_code_replay_is_refused(self):
        code = self._code()
        self.assertEqual(self._handoff(code).status_code, 303)
        replay = self._handoff(code, client=Client())
        self.assertEqual(replay.status_code, 403)

    def test_code_from_another_device_is_refused_and_not_burnt(self):
        other_device, other_access, _ = make_device(self.user, 'password_totp', name='Other')
        code = self._code()
        r = self._handoff(code, access=other_access)
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('_auth_user_id', self.web.session)
        # The rightful device can still use it.
        self.assertEqual(self._handoff(code, client=Client()).status_code, 303)

    def test_another_users_bearer_is_refused(self):
        other = make_user('ho-2')
        _, other_access, _ = make_device(other)
        self.assertEqual(self._handoff(self._code(), access=other_access).status_code, 403)

    def test_missing_bearer_is_refused(self):
        code = self._code()
        r = self.web.post('/m/handoff/', {'code': code})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('Location', r)
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_bad_bearer_is_refused(self):
        self.assertEqual(self._handoff(self._code(), access='nope').status_code, 403)

    def test_expired_code_is_refused(self):
        code = self._code()
        MobileAuthTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._handoff(code).status_code, 403)

    def test_revoked_device_cannot_hand_off(self):
        code = self._code()
        self.device.revoke('test')
        self.assertEqual(self._handoff(code).status_code, 403)

    def test_get_is_refused(self):
        r = self.web.get('/m/handoff/', {'code': self._code()}, **bearer(self.access))
        self.assertEqual(r.status_code, 405)
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_a_ws_ticket_is_not_a_handoff_code(self):
        r = self.api.post('/api/mobile/v1/ws-ticket/', **bearer(self.access))
        self.assertEqual(self._handoff(r.json()['ticket']).status_code, 403)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class MobileSessionGuardTests(TestCase):

    def setUp(self):
        self.user = make_user('guard-1')
        self.device, self.access, _ = make_device(self.user, 'password')
        self.web = Client()
        code = Client().post('/api/mobile/v1/auth/web-session/', **bearer(self.access)).json()['code']
        r = self.web.post('/m/handoff/', {'code': code, 'next': PROTECTED}, **bearer(self.access))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.web.get(PROTECTED).status_code, 200)

    def _assert_logged_out(self):
        r = self.web.get(PROTECTED)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login/', r['Location'])
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_revoked_device_logs_its_web_session_out(self):
        self.device.revoke('test')
        self._assert_logged_out()

    def test_api_paths_get_a_401(self):
        self.device.revoke('test')
        r = self.web.get('/api/notifications/unread-count/')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['code'], 'mobile_device_signed_out')

    def test_password_change_logs_it_out(self):
        self.user.set_password('a-new-password-1')
        self.user.save()
        r = self.web.get(PROTECTED)
        self.assertEqual(r.status_code, 302)

    def test_quarantine_logs_it_out(self):
        type(self.user).objects.filter(pk=self.user.pk).update(is_quarantined=True)
        self._assert_logged_out()

    def test_deleted_device_logs_it_out(self):
        self.device.delete()
        self._assert_logged_out()

    def test_device_of_another_user_in_the_session_logs_it_out(self):
        other = make_user('guard-2')
        other_device, _, _ = make_device(other)
        session = self.web.session
        session['mobile_device_id'] = other_device.pk
        session.save()
        self._assert_logged_out()

    def test_ordinary_sessions_are_untouched(self):
        plain = Client()
        plain.force_login(make_user('guard-3'))
        self.assertEqual(plain.get(PROTECTED).status_code, 200)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class InMobileAppLayoutTests(TestCase):

    def setUp(self):
        self.user = make_user('lay-1')

    def test_navbar_hidden_for_app_user_agent_and_vary_set(self):
        c = Client(HTTP_USER_AGENT='Mozilla/5.0 ParliamentApp/1.0.0')
        c.force_login(self.user)
        r = c.get(PROTECTED)
        self.assertTrue(r.context['in_mobile_app'])
        self.assertIn('User-Agent', r['Vary'])
        self.assertContains(r, 'shadow-lg relative z-10 hidden')

    def test_navbar_shown_for_a_browser(self):
        c = Client(HTTP_USER_AGENT='Mozilla/5.0 Safari')
        c.force_login(self.user)
        r = c.get(PROTECTED)
        self.assertFalse(r.context['in_mobile_app'])
        self.assertNotContains(r, 'shadow-lg relative z-10 hidden')

    def test_handoff_session_is_in_mobile_app_without_the_ua(self):
        device, access, _ = make_device(self.user)
        code = Client().post('/api/mobile/v1/auth/web-session/', **bearer(access)).json()['code']
        web = Client(HTTP_USER_AGENT='Mozilla/5.0 Safari')
        web.post('/m/handoff/', {'code': code}, **bearer(access))
        self.assertTrue(web.get(PROTECTED).context['in_mobile_app'])
