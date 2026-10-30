"""auth/refresh/, auth/logout/, me/, and the per-request device checks."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from src.mobile import tokens
from src.models.mobile import MobileDevice
from src.models_feature_flags import FeatureFlag, PageToggle
from src.tests.mobile.base import MobileTestCase, make_user


class RefreshTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('m300')
        self.pair = self.login(self.user.username).json()

    def refresh(self, raw):
        return self.post('auth/refresh/', {'refresh_token': raw})

    def test_rotation_returns_a_new_pair(self):
        r = self.refresh(self.pair['refresh_token'])
        self.assertEqual(r.status_code, 200, r.content)
        new = r.json()
        self.assertNotEqual(new['access_token'], self.pair['access_token'])
        self.assertNotEqual(new['refresh_token'], self.pair['refresh_token'])
        self.assertEqual(new['device_id'], self.pair['device_id'])
        # The old access token is gone; the new one works.
        self.assertEqual(self.get_me(self.pair['access_token']).status_code, 401)
        self.assertEqual(self.get_me(new['access_token']).status_code, 200)

    def test_replay_within_grace_returns_a_new_pair(self):
        self.assertEqual(self.refresh(self.pair['refresh_token']).status_code, 200)
        r = self.refresh(self.pair['refresh_token'])     # first response "lost"
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get_me(r.json()['access_token']).status_code, 200)

    def test_replay_after_grace_revokes_the_device(self):
        newer = self.refresh(self.pair['refresh_token']).json()
        later = timezone.now() + tokens.REFRESH_GRACE + timedelta(seconds=5)
        with patch('django.utils.timezone.now', return_value=later):
            r = self.refresh(self.pair['refresh_token'])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'reused')
        self.assertTrue(MobileDevice.objects.get(user=self.user).is_revoked)
        # Even the legitimately rotated pair is dead now.
        self.assertEqual(self.refresh(newer['refresh_token']).json()['error'], 'revoked')
        self.assertEqual(self.get_me(newer['access_token']).status_code, 401)

    def test_unknown_refresh_token(self):
        r = self.refresh('not-a-real-token')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {'error': 'invalid'})
        self.assertEqual(self.post('auth/refresh/', {}).status_code, 400)

    def test_password_change_signs_the_device_out(self):
        self.user.set_password('a-brand-new-password-77!')
        self.user.save()
        self.assertEqual(self.refresh(self.pair['refresh_token']).json()['error'], 'password_changed')
        r = self.get_me(self.pair['access_token'])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'signed_out_password_changed')


class BearerChecksTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = make_user('m310')
        self.pair = self.login(self.user.username).json()
        self.device = MobileDevice.objects.get(user=self.user)

    def test_revoked_device(self):
        self.device.revoke('test')
        r = self.get_me(self.pair['access_token'])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'signed_out_revoked')

    def test_expired_access_token(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            access_expires_at=timezone.now() - timedelta(seconds=1))
        r = self.get_me(self.pair['access_token'])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'token_expired')

    def test_expired_refresh_window(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            refresh_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.get_me(self.pair['access_token']).json()['error'], 'signed_out_expired')

    def test_no_token_is_401(self):
        r = self.api.get('/api/mobile/v1/me/')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()['error'], 'not_authenticated')

    def test_logout_revokes_this_device_only(self):
        other = self.login(self.user.username).json()
        r = self.api.post('/api/mobile/v1/auth/logout/', **self.bearer(self.pair['access_token']))
        self.assertEqual(r.status_code, 200)
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_revoked)
        self.assertEqual(self.get_me(self.pair['access_token']).status_code, 401)
        self.assertEqual(self.get_me(other['access_token']).status_code, 200)
        self.assertEqual(self.post('auth/refresh/', {'refresh_token': self.pair['refresh_token']})
                         .json()['error'], 'revoked')

    def test_logout_needs_a_token(self):
        self.assertEqual(self.api.post('/api/mobile/v1/auth/logout/').status_code, 401)


class MeTests(MobileTestCase):

    def test_me_profile_flags_and_pages(self):
        user = make_user('m320', preferred_name='Moby', name='Mobile Tester', member_type='Officer')
        FeatureFlag.objects.create(name='chats', display_name='Chats', description='x', is_enabled=False)
        PageToggle.objects.create(url_name='vote', display_name='Vote', is_enabled=False)
        access = self.login(user.username).json()['access_token']
        r = self.get_me(access)
        self.assertEqual(r.status_code, 200)
        me = r.json()
        self.assertEqual(me['user_id'], 'm320')
        self.assertEqual(me['display_name'], 'Moby Tester')
        self.assertEqual(me['member_type'], 'Officer')
        self.assertEqual(me['member_status'], 'Active')
        self.assertTrue(me['is_officer'])
        self.assertFalse(me['is_admin'])
        self.assertIn('can_vote', me)
        self.assertFalse(me['features']['chats'])
        self.assertTrue(me['features']['committee_voting'])   # unseeded flag fails open, like views
        self.assertEqual(set(me['features']), {
            'chats', 'legislation_voting', 'committee_voting', 'calendar_subscriptions',
            'announcements', 'qr_checkin', 'location_checkin'})
        # qr_attendance_checkin is disabled by default, so check-in is off until seeded.
        self.assertFalse(me['features']['qr_checkin'])
        self.assertFalse(me['pages']['vote'])
        self.assertTrue(me['pages']['home'])
        self.assertEqual(set(me['pages']), {'home', 'vote', 'calendar', 'chat_index'})
        self.assertEqual(me['device']['verified_via'], 'password')
        self.assertTrue(me['can_post_announcements'])

    def test_app_update_comes_from_site_settings(self):
        from src.models_feature_flags import SiteSetting
        from src.view.admin_v2 import APP_UPDATE_DEFAULTS, seed_dashboard_defaults
        user = make_user('m321')
        access = self.login(user.username).json()['access_token']
        self.assertEqual(self.get_me(access).json()['app_update'], {'latest_build': 0, 'min_build': 0, 'url': ''})
        seed_dashboard_defaults()
        self.assertEqual(SiteSetting.objects.filter(key__startswith='mobile_').count(), len(APP_UPDATE_DEFAULTS))
        SiteSetting.set_setting('mobile_ios_latest_build', 14)
        SiteSetting.set_setting('mobile_ios_min_build', 9)
        SiteSetting.set_setting('mobile_ios_update_url', 'https://testflight.apple.com/join/abc')
        SiteSetting.set_setting('mobile_android_latest_build', 99)
        self.assertEqual(self.get_me(access).json()['app_update'],          # the device is iOS
                         {'latest_build': 14, 'min_build': 9, 'url': 'https://testflight.apple.com/join/abc'})

    def test_can_post_announcements_matches_the_announcement_page(self):
        # Same rule as officer_required on create_announcement: officers,
        # chairs and admins; not members, advisors or pledges.
        cases = [('m331', 'Chair', True), ('m332', 'Member', False),
                 ('m333', 'Advisor', False), ('m334', 'Pledge', False)]
        for uid, member_type, expected in cases:
            with self.subTest(member_type=member_type):
                user = make_user(uid, member_type=member_type)
                access = self.login(user.username).json()['access_token']
                self.assertIs(self.get_me(access).json()['can_post_announcements'], expected)
                web = self.client_class()
                web.force_login(user)
                status = web.get('/officers/announcements/create/').status_code
                self.assertEqual(status == 200, expected, status)


class ThrottleTests(MobileTestCase):

    def test_login_is_throttled_per_ip(self):
        statuses = [self.post('auth/login/', {}).status_code for _ in range(11)]
        self.assertEqual(statuses[:10], [400] * 10)
        self.assertEqual(statuses[10], 429)
        body = self.post('auth/login/', {}).json()
        self.assertEqual(body['error'], 'throttled')
        self.assertGreater(body['retry_after'], 0)

    def test_device_id_header_has_its_own_bucket(self):
        from src.mobile import api
        # Lift the per-IP bucket so only the per-device one (30/min for refresh) bites.
        with patch.object(api.MobileAuthIPThrottle, 'get_rate_for', return_value='1000/min'):
            hdr = {'HTTP_X_DEVICE_ID': 'install-abc'}
            codes = [self.post('auth/refresh/', {'refresh_token': 'x'}, **hdr).status_code
                     for _ in range(31)]
            self.assertEqual(codes[-1], 429)
            # Another install from the same IP is unaffected.
            self.assertEqual(self.post('auth/refresh/', {'refresh_token': 'x'},
                                       HTTP_X_DEVICE_ID='install-xyz').status_code, 401)
