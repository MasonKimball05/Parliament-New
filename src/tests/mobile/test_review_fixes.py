"""
Mobile plan, Phase 1 — fixes from the independent security review.

1. a password-only device stops working once a second factor exists / is required;
2. web-view sessions carry the app sign-in's geo flag (non-US export block);
3. refresh grace replay: once, never silently, reuse revokes + alerts;
4. pushes only go to devices that are still allowed;
5. a dead device's web-view session can't open websockets;
6. absolute device age cap;
7. the handoff starts from an empty session;
8. a dead device's cookie never turns /m/handoff/ into a login redirect;
9. push deep links are same-site paths only;
10. security / excuse pushes carry a generic body.
"""
import json
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.mobile import tokens
from src.models import ChatChannel, Notification, ParliamentUser, TwoFactorRequirement
from src.models.mobile import MobileDevice
from src.models_feature_flags import SiteSetting
from src.tests.mobile.base import MobileTestCase, make_user as make_pw_user
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

PROTECTED = '/account/sessions/'
TOKEN_A = 'ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]'
FOREIGN_GEO = {
    'country': 'France', 'country_code': 'FR', 'city': 'Paris', 'region': 'IDF',
    'lat': 48.85, 'lon': 2.35, 'org': '', 'as': '',
}


def _handoff(web, access, next_url=PROTECTED, **extra):
    code = Client().post('/api/mobile/v1/auth/web-session/', **bearer(access)).json()['code']
    return web.post('/m/handoff/', {'code': code, 'next': next_url}, **bearer(access), **extra)


# ── 1. password-only devices vs. a second factor ─────────────────────────────

class PasswordDeviceSecondFactorTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('sf-1')
        self.device, self.access, _ = make_device(self.user, 'password')

    def _allowed(self):
        device = MobileDevice.objects.select_related('user').get(pk=self.device.pk)
        return device.still_allowed()

    def test_allowed_while_member_has_no_second_factor(self):
        self.assertEqual(self._allowed(), (True, ''))

    def test_enrolling_totp_signs_the_password_device_out(self):
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.assertEqual(self._allowed(), (False, 'second_factor_required'))
        r = Client().get('/api/mobile/v1/me/', **bearer(self.access))
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'signed_out_second_factor_required')

    def test_unconfirmed_totp_does_not(self):
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=False)
        self.assertEqual(self._allowed(), (True, ''))

    def test_individual_requirement_signs_it_out(self):
        TwoFactorRequirement.objects.create(user=self.user, requirement='required')
        self.assertEqual(self._allowed(), (False, 'second_factor_required'))

    def test_policy_signs_it_out(self):
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode', defaults={'value': 'all_members', 'setting_type': 'string'})
        cache.clear()
        self.assertEqual(self._allowed(), (False, 'second_factor_required'))

    def test_refresh_is_refused(self):
        _, _, refresh = make_device(self.user, 'password')
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.assertEqual(tokens.rotate(refresh), (None, None, 'second_factor_required'))

    def test_handoff_is_refused(self):
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        web = Client()
        code_r = Client().post('/api/mobile/v1/auth/web-session/', **bearer(self.access))
        self.assertEqual(code_r.status_code, 401)
        r = web.post('/m/handoff/', {'code': 'x'}, **bearer(self.access))
        self.assertEqual(r.status_code, 403)

    def test_verified_devices_cost_no_extra_queries(self):
        user = make_user('sf-2')
        TOTPDevice.objects.create(user=user, name='phone', confirmed=True)
        device, _, _ = make_device(user, 'password_totp')
        device = MobileDevice.objects.select_related('user').get(pk=device.pk)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(device.still_allowed(), (True, ''))
        self.assertEqual(len(ctx.captured_queries), 0)


# ── 2. geo flag from the app sign-in reaches the web view ────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class HandoffGeoTests(MobileTestCase):

    def test_app_sign_in_records_geo_on_the_device(self):
        user = make_pw_user('geo1')
        with mock.patch('src.geo_utils.get_ip_geo', return_value=FOREIGN_GEO):
            r = self.login(user.username)
        self.assertEqual(r.status_code, 200, r.content)
        device = MobileDevice.objects.get(user=user)
        self.assertTrue(device.login_geo_suspicious)
        self.assertEqual(device.login_geo['country_code'], 'FR')

    def test_us_sign_in_is_not_flagged(self):
        user = make_pw_user('geo2')
        self.assertEqual(self.login(user.username).status_code, 200)
        device = MobileDevice.objects.get(user=user)
        self.assertFalse(device.login_geo_suspicious)
        self.assertEqual(device.login_geo['country_code'], 'US')

    def test_handoff_writes_the_web_pipeline_session_keys_and_blocks_exports(self):
        user = make_pw_user('geo3')
        with mock.patch('src.geo_utils.get_ip_geo', return_value=FOREIGN_GEO):
            access = self.login(user.username).json()['access_token']
        web = Client()
        r = _handoff(web, access)
        self.assertEqual(r.status_code, 303)
        session = web.session
        self.assertIs(session['login_geo_suspicious'], True)
        self.assertEqual(session['login_geo_country'], 'France')
        self.assertEqual(session['login_geo_city'], 'Paris')
        self.assertEqual(session['login_geo']['country_code'], 'FR')
        blocked = web.get(reverse('export_directory'))
        self.assertEqual(blocked.status_code, 403)
        self.assertTemplateUsed(blocked, 'geo_restricted.html')

    def test_us_handoff_is_not_flagged(self):
        user = make_pw_user('geo4')
        access = self.login(user.username).json()['access_token']
        web = Client()
        _handoff(web, access)
        self.assertIs(web.session['login_geo_suspicious'], False)

    def test_handoff_ip_is_re_evaluated_from_the_cache(self):
        user = make_pw_user('geo5')
        access = self.login(user.username).json()['access_token']
        cache.set('geo_203.0.113.9', FOREIGN_GEO, 60)
        web = Client()
        _handoff(web, access, REMOTE_ADDR='203.0.113.9')
        self.assertIs(web.session['login_geo_suspicious'], True)
        self.assertEqual(web.session['login_geo_country'], 'France')

    def test_handoff_never_makes_an_uncached_lookup(self):
        user = make_pw_user('geo6')
        access = self.login(user.username).json()['access_token']
        web = Client()
        with mock.patch('src.geo_utils.requests.get', side_effect=AssertionError('network')) as get:
            r = _handoff(web, access, REMOTE_ADDR='203.0.113.10')
        self.assertEqual(r.status_code, 303)
        get.assert_not_called()
        self.assertIs(web.session['login_geo_suspicious'], False)


# ── 3. refresh grace replay ──────────────────────────────────────────────────

class RefreshGraceTests(TestCase):

    def setUp(self):
        self.user = make_user('rg-1')
        self.device, self.access, self.p = make_device(self.user, 'password_totp')

    def _revoked(self):
        return MobileDevice.objects.get(pk=self.device.pk).is_revoked

    def test_normal_rotation(self):
        device, access, c1 = tokens.rotate(self.p)
        self.assertIsNotNone(device)
        device, access, c2 = tokens.rotate(c1)
        self.assertIsNotNone(device)
        self.assertFalse(self._revoked())

    def test_one_grace_replay_is_allowed_and_both_answers_stay_usable_until_one_is_used(self):
        _, _, c1 = tokens.rotate(self.p)
        dev, _, c2 = tokens.rotate(self.p)       # the first response was lost
        self.assertIsNotNone(dev)
        self.assertFalse(self._revoked())
        # The app kept the first answer: it still works (it was not orphaned).
        dev, _, c3 = tokens.rotate(c1)
        self.assertIsNotNone(dev)
        self.assertFalse(self._revoked())

    def test_second_replay_of_the_previous_token_revokes_and_alerts(self):
        tokens.rotate(self.p)
        tokens.rotate(self.p)
        with mock.patch('src.security_notifications.notify_user_security_event') as notify:
            self.assertEqual(tokens.rotate(self.p), (None, None, 'reused'))
        self.assertTrue(self._revoked())
        notify.assert_called_once()
        self.assertEqual(notify.call_args.args[0], self.user)

    def test_superseded_token_reuse_revokes_and_alerts(self):
        _, _, c1 = tokens.rotate(self.p)
        _, _, c2 = tokens.rotate(self.p)          # grace replay: c1 and c2 both live
        _, _, c3 = tokens.rotate(c2)              # c2's holder moved on: c1 is superseded
        self.assertFalse(self._revoked())
        with mock.patch('src.security_notifications.notify_user_security_event') as notify:
            self.assertEqual(tokens.rotate(c1), (None, None, 'reused'))
        self.assertTrue(self._revoked())
        notify.assert_called_once()

    def test_grace_replay_after_the_window_revokes(self):
        tokens.rotate(self.p)
        later = timezone.now() + tokens.REFRESH_GRACE + timedelta(seconds=5)
        with mock.patch('django.utils.timezone.now', return_value=later), \
                mock.patch('src.security_notifications.notify_user_security_event') as notify:
            self.assertEqual(tokens.rotate(self.p), (None, None, 'reused'))
        self.assertTrue(self._revoked())
        notify.assert_called_once()

    def test_older_token_reuse_revokes(self):
        _, _, c1 = tokens.rotate(self.p)
        _, _, c2 = tokens.rotate(c1)
        _, _, c3 = tokens.rotate(c2)
        with mock.patch('src.security_notifications.notify_user_security_event'):
            self.assertEqual(tokens.rotate(self.p), (None, None, 'reused'))
        self.assertTrue(self._revoked())


# ── 4. pushes only to devices that are still allowed ─────────────────────────

def _expo_ok(url, json=None, **kw):
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {'data': [{'status': 'ok', 'id': f't{i}'} for i, _ in enumerate(json)]}
    return resp


class PushRecipientFilterTests(TestCase):

    def setUp(self):
        self.user = make_user('pf-1')
        self.device, _, _ = make_device(self.user, 'password_totp')
        MobileDevice.objects.filter(pk=self.device.pk).update(push_token=TOKEN_A)
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)

    def _send(self):
        from src.tasks import send_native_push
        with mock.patch('src.mobile.push.requests.post', side_effect=_expo_ok) as post:
            send_native_push.delay(self.user.pk, 'T', 'B')
        return post

    def _token(self):
        return MobileDevice.objects.get(pk=self.device.pk).push_token

    def test_live_device_gets_the_push(self):
        self.assertEqual(self._send().call_count, 1)
        self.assertEqual(self._token(), TOKEN_A)

    def test_password_change(self):
        self.user.set_password('changed-pw-1234')
        self.user.save()
        self._send().assert_not_called()
        self.assertEqual(self._token(), '')

    def test_quarantined(self):
        ParliamentUser.objects.filter(pk=self.user.pk).update(is_quarantined=True)
        self._send().assert_not_called()
        self.assertEqual(self._token(), '')

    def test_removed(self):
        ParliamentUser.objects.filter(pk=self.user.pk).update(member_status='Removed')
        self._send().assert_not_called()
        self.assertEqual(self._token(), '')

    def test_refresh_expired(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            refresh_expires_at=timezone.now() - timedelta(seconds=1))
        self._send().assert_not_called()
        self.assertEqual(self._token(), '')

    def test_event_reminder_subscriber_set_uses_the_same_filter(self):
        from src.mobile.push import live_push_devices
        other = make_user('pf-2')
        d2, _, _ = make_device(other, 'password_totp')
        MobileDevice.objects.filter(pk=d2.pk).update(push_token='ExponentPushToken[bbbbbbbbbbbbbbbbbbbb]')
        ParliamentUser.objects.filter(pk=other.pk).update(is_quarantined=True)
        live = live_push_devices([self.user.pk, other.pk])
        self.assertEqual([d.pk for d in live], [self.device.pk])
        self.assertEqual(MobileDevice.objects.get(pk=d2.pk).push_token, '')


# ── 5. websockets from a dead device's web-view session ──────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class WebViewSessionWebsocketTests(TransactionTestCase):

    def setUp(self):
        from Parliament.asgi import application
        self.application = application
        self.user = make_user('wsg-1')
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.device, self.access, _ = make_device(self.user, 'password_totp')
        self.channel = ChatChannel.objects.create(
            name='General', channel_type='custom', access_type='open', created_by=self.user)
        self.web = Client()
        self.assertEqual(_handoff(self.web, self.access).status_code, 303)
        self.cookie = self.web.cookies[settings.SESSION_COOKIE_NAME].value

    def _connect(self):
        headers = [(b'origin', b'http://testserver'), (b'host', b'testserver'),
                   (b'cookie', f'{settings.SESSION_COOKIE_NAME}={self.cookie}'.encode())]

        async def run():
            comm = WebsocketCommunicator(self.application, f'/ws/chat/{self.channel.pk}/',
                                         headers=headers)
            connected, _ = await comm.connect()
            await comm.disconnect()
            return connected
        return async_to_sync(run)()

    def test_live_device_session_connects(self):
        self.assertTrue(self._connect())

    def test_revoked_device_session_is_refused(self):
        self.device.revoke('test')
        self.assertFalse(self._connect())

    def test_password_change_session_is_refused(self):
        self.user.set_password('changed-pw-1234')
        self.user.save()
        self.assertFalse(self._connect())


# ── 6. absolute device age ───────────────────────────────────────────────────

class DeviceMaxAgeTests(TestCase):

    def setUp(self):
        self.user = make_user('age-1')
        self.device, _, self.refresh = make_device(self.user, 'password_totp')

    def _age(self, days):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            created_at=timezone.now() - timedelta(days=days))

    def test_setting(self):
        self.assertEqual(settings.MOBILE_DEVICE_MAX_AGE_DAYS, 90)

    def test_rotation_never_extends_past_the_cap(self):
        self._age(80)
        device, _, _ = tokens.rotate(self.refresh)
        cap = device.created_at + timedelta(days=90)
        self.assertLessEqual(device.refresh_expires_at, cap)
        self.assertGreater(device.refresh_expires_at, timezone.now() + timedelta(days=9))

    def test_still_allowed_fails_after_the_cap(self):
        self._age(91)
        device = MobileDevice.objects.select_related('user').get(pk=self.device.pk)
        self.assertEqual(device.still_allowed(), (False, 'reauth_required'))
        self.assertEqual(tokens.rotate(self.refresh), (None, None, 'reauth_required'))


# ── 7 / 8. handoff session hygiene ───────────────────────────────────────────

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class HandoffSessionHygieneTests(TestCase):

    def setUp(self):
        self.user = make_user('hy-1')

    def test_handoff_flushes_prior_session_state_for_the_same_user(self):
        device, access, _ = make_device(self.user, 'password')
        web = Client()
        web.force_login(self.user)
        session = web.session
        session['webauthn_authenticated'] = True
        session['stale_marker'] = 'x'
        session.save()
        self.assertEqual(_handoff(web, access).status_code, 303)
        session = web.session
        self.assertNotIn('webauthn_authenticated', session)
        self.assertNotIn('stale_marker', session)
        self.assertEqual(session['mobile_device_id'], device.pk)

    def test_cookie_from_a_revoked_device_does_not_redirect_the_handoff_to_login(self):
        old, old_access, _ = make_device(self.user, 'password')
        web = Client()
        self.assertEqual(_handoff(web, old_access).status_code, 303)
        old.revoke('signed out')
        new, new_access, _ = make_device(self.user, 'password')
        r = _handoff(web, new_access)
        self.assertEqual(r.status_code, 303, r.get('Location'))
        self.assertEqual(r['Location'], PROTECTED)
        self.assertEqual(web.session['mobile_device_id'], new.pk)

    def test_dead_cookie_and_bad_code_is_a_403_not_a_login_form(self):
        old, old_access, _ = make_device(self.user, 'password')
        web = Client()
        _handoff(web, old_access)
        old.revoke('signed out')
        r = web.post('/m/handoff/', {'code': 'nope'}, **bearer(old_access))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('_auth_user_id', web.session)


# ── 9. deep links ────────────────────────────────────────────────────────────

class PushDeepLinkSafetyTests(TestCase):

    def test_external_and_odd_urls_become_home(self):
        from src.mobile.deeplinks import push_data_for_url
        for bad in ('https://evil.example/x', '//evil.example/x', 'javascript:alert(1)',
                    '/\\evil.example/', 'evil/relative', '\t//evil.example', '', None, 42):
            data = push_data_for_url(bad)
            self.assertEqual(data['url'], '/home/', bad)
            self.assertEqual(data['screen'], 'home', bad)

    def test_same_site_paths_pass_through(self):
        from src.mobile.deeplinks import push_data_for_url
        self.assertEqual(push_data_for_url('/legislation/detail/7/?x=1#y'),
                         {'url': '/legislation/detail/7/?x=1#y', 'screen': 'vote', 'id': 7})
        self.assertEqual(push_data_for_url('/preferences/')['url'], '/preferences/')


# ── 10. generic lock-screen text for sensitive types ─────────────────────────

class SensitivePushBodyTests(TestCase):

    def setUp(self):
        self.user = make_user('pb-1')

    def _pushes(self, fn):
        with mock.patch('src.tasks.send_push_notification') as task:
            fn()
        return [c.kwargs for c in task.delay.call_args_list]

    def test_security_alert_body_is_generic(self):
        from src.security_notifications import notify_user_security_event
        calls = self._pushes(lambda: notify_user_security_event(
            self.user, 'New sign-in', 'Signed in from Paris, France with passkey "Work laptop"'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['body'], 'New security alert on your account')
        self.assertNotIn('Paris', json.dumps(calls[0]))
        n = Notification.objects.get(recipient=self.user, notification_type='security')
        self.assertIn('Paris', n.message)

    def test_excuse_review_body_is_generic(self):
        from src.notification_service import create_notification
        calls = self._pushes(lambda: create_notification(
            self.user, 'excuse_reviewed', 'Your excuse was denied',
            'Officer notes: medical note missing', link='/excuses/'))
        self.assertEqual(calls[0]['body'], 'Your excuse was reviewed')
        self.assertNotIn('medical', json.dumps(calls[0]))

    def test_other_types_keep_the_preview(self):
        from src.notification_service import create_notification
        calls = self._pushes(lambda: create_notification(
            self.user, 'announcement', 'News', 'Chapter meeting moved'))
        self.assertEqual(calls[0]['body'], 'Chapter meeting moved')
