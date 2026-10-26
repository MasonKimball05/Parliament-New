"""
v3.52.0 client-contract fixes found while building the app:

* ``/m/handoff/`` takes the access token as the POST field ``access_token``
  (Android's web view drops headers on a POST load), never from the query string;
* every Bearer view answers errors as ``{"error": code, ...}``;
* Bearer views use their own ``mobile`` throttle scope;
* ``me/`` carries ``links.education``;
* a locked IP doesn't burn the 2FA ticket.
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from src import login_pipeline as lp
from src.mobile import api
from src.models import Committee
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.models_feature_flags import PageToggle
from src.tests.mobile.base import MobileTestCase, make_user as base_make_user, totp_code, totp_device
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

HANDOFF = '/m/handoff/'
PROTECTED = '/account/sessions/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class HandoffBodyTokenTests(TestCase):

    def setUp(self):
        self.user = make_user('hb-1')
        TOTPDevice.objects.create(user=self.user, name='phone', confirmed=True)
        self.device, self.access, _ = make_device(self.user, 'password_totp')
        self.web = Client()

    def _code(self, access=None):
        r = Client().post('/api/mobile/v1/auth/web-session/', **bearer(access or self.access))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['code']

    def _post(self, data, path=HANDOFF, client=None, **headers):
        return (client or self.web).post(path, data, **headers)

    def test_body_token_without_header_signs_in(self):
        """The Android request: code + access_token + next in the form body, no header."""
        r = self._post({'code': self._code(), 'access_token': self.access, 'next': PROTECTED})
        self.assertEqual(r.status_code, 303, r.content)
        self.assertEqual(r['Location'], PROTECTED)
        self.assertEqual(r['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.web.session['mobile_device_id'], self.device.pk)
        self.assertEqual(self.web.get(PROTECTED).status_code, 200)

    def test_header_and_matching_body_token_signs_in(self):
        r = self._post({'code': self._code(), 'access_token': self.access}, **bearer(self.access))
        self.assertEqual(r.status_code, 303)

    def test_header_and_body_naming_different_tokens_is_refused(self):
        _, other_access, _ = make_device(self.user, 'password_totp', name='Other')
        r = self._post({'code': self._code(), 'access_token': other_access}, **bearer(self.access))
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_token_in_query_string_is_refused_even_when_valid(self):
        code = self._code()
        r = self._post({'code': code}, path=f'{HANDOFF}?access_token={self.access}')
        self.assertEqual(r.status_code, 403)
        r = self._post({'code': code, 'access_token': self.access},
                       path=f'{HANDOFF}?access_token={self.access}')
        self.assertEqual(r.status_code, 403)
        self.assertNotIn('_auth_user_id', self.web.session)
        # Refused before the code was looked at: still usable.
        self.assertEqual(self._post({'code': code, 'access_token': self.access}).status_code, 303)

    def test_code_in_query_string_is_refused(self):
        r = self._post({'access_token': self.access}, path=f'{HANDOFF}?code={self._code()}')
        self.assertEqual(r.status_code, 403)

    def test_body_token_of_another_device_is_refused_and_code_not_burnt(self):
        _, other_access, _ = make_device(self.user, 'password_totp', name='Other')
        code = self._code()
        self.assertEqual(self._post({'code': code, 'access_token': other_access}).status_code, 403)
        self.assertEqual(self._post({'code': code, 'access_token': self.access},
                                    client=Client()).status_code, 303)

    def test_body_token_of_another_user_is_refused(self):
        _, other_access, _ = make_device(make_user('hb-2'))
        self.assertEqual(self._post({'code': self._code(), 'access_token': other_access}).status_code, 403)

    def test_expired_or_revoked_body_token_is_refused(self):
        code = self._code()
        MobileDevice.objects.filter(pk=self.device.pk).update(
            access_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self._post({'code': code, 'access_token': self.access}).status_code, 403)
        device, access, _ = make_device(self.user, 'password_totp', name='Second')
        code = self._code(access)
        device.revoke('test')
        self.assertEqual(self._post({'code': code, 'access_token': access}).status_code, 403)

    def test_bad_and_missing_tokens_are_refused(self):
        code = self._code()
        for data in ({'code': code}, {'code': code, 'access_token': ''},
                     {'code': code, 'access_token': 'nope'},
                     {'code': code, 'access_token': 'x' * 5000}):
            self.assertEqual(self._post(data).status_code, 403, data.get('access_token', '')[:10])
        self.assertNotIn('_auth_user_id', self.web.session)

    def test_body_token_still_needs_the_code(self):
        self.assertEqual(self._post({'access_token': self.access}).status_code, 403)
        self.assertEqual(self._post({'access_token': self.access, 'code': 'guess'}).status_code, 403)

    def test_cross_site_post_is_refused(self):
        """Login CSRF: someone posting THEIR OWN code + token from their page."""
        data = {'code': self._code(), 'access_token': self.access}
        self.assertEqual(self._post(data, HTTP_SEC_FETCH_SITE='cross-site').status_code, 403)
        self.assertEqual(self._post(data, HTTP_ORIGIN='https://evil.example').status_code, 403)
        self.assertNotIn('_auth_user_id', self.web.session)
        # The app's own browser-initiated load (and a same-origin one) is fine.
        self.assertEqual(self._post(data, HTTP_SEC_FETCH_SITE='none').status_code, 303)
        data = {'code': self._code(), 'access_token': self.access}
        self.assertEqual(self._post(data, client=Client(), HTTP_ORIGIN='http://testserver',
                                    HTTP_SEC_FETCH_SITE='same-origin').status_code, 303)

    def test_token_is_redacted_from_error_reports(self):
        from django.test import RequestFactory
        from django.views.debug import SafeExceptionReporterFilter
        from src.mobile.handoff import mobile_web_handoff
        request = RequestFactory().post(HANDOFF, {'access_token': 'secret-tok', 'code': 'c'})
        with patch('src.mobile.handoff._authenticate', return_value=None):
            self.assertEqual(mobile_web_handoff(request).status_code, 403)
        self.assertEqual(sorted(request.sensitive_post_parameters), ['access_token', 'code'])
        cleansed = SafeExceptionReporterFilter().get_post_parameters(request)
        self.assertNotEqual(cleansed['access_token'], 'secret-tok')

    def test_input_sanitizer_never_scans_or_logs_the_token(self):
        from src.middleware.security import InputSanitizationMiddleware
        self.assertIn('access_token', InputSanitizationMiddleware(lambda r: None).skip_fields)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class UniformErrorShapeTests(TestCase):
    """Every Bearer endpoint answers {"error": code} — the codes the app keys on."""

    ROUTES = [
        ('post', '/api/mobile/v1/auth/web-session/'),
        ('post', '/api/mobile/v1/ws-ticket/'),
        ('get', '/api/mobile/v1/devices/'),
        ('post', '/api/mobile/v1/devices/push-token/'),
        ('get', '/api/mobile/v1/me/'),
        ('post', '/api/mobile/v1/auth/logout/'),
    ]

    def setUp(self):
        self.user = make_user('err-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def _call(self, method, path, **headers):
        return getattr(self.api, method)(path, **headers)

    def _assert_error(self, expected, **headers):
        for method, path in self.ROUTES:
            r = self._call(method, path, **headers)
            self.assertEqual(r.status_code, 401, path)
            body = r.json()
            self.assertEqual(body.get('error'), expected, f'{path}: {body}')
            self.assertIn('no-store', r['Cache-Control'], path)

    def test_no_token(self):
        self._assert_error('not_authenticated')

    def test_unknown_token(self):
        self._assert_error('invalid_token', **bearer('nope'))

    def test_malformed_header(self):
        self._assert_error('invalid_token', HTTP_AUTHORIZATION='Bearer a b')

    def test_expired_token(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            access_expires_at=timezone.now() - timedelta(seconds=1))
        self._assert_error('token_expired', **bearer(self.access))

    def test_signed_out_reason(self):
        self.device.revoke('test')
        self._assert_error('signed_out_revoked', **bearer(self.access))

    def test_unknown_device_revoke_is_error_not_found(self):
        import uuid
        r = self.api.post(f'/api/mobile/v1/devices/{uuid.uuid4()}/revoke/', **bearer(self.access))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error'], 'not_found')

    def test_wrong_method_is_coded(self):
        r = self.api.get('/api/mobile/v1/auth/web-session/', **bearer(self.access))
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r.json()['error'], 'method_not_allowed')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class MobileThrottleScopeTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('thr-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()

    def test_bearer_views_use_the_mobile_scope_not_the_site_default(self):
        from src.tests.guards.test_mobile_views_use_mobile_auth import (
            PRE_AUTH, _mobile_patterns, _view_class)
        for name, callback in _mobile_patterns():
            cls = _view_class(callback)
            throttles = list(cls.throttle_classes)
            if name in PRE_AUTH:
                self.assertEqual(throttles, [api.MobileAuthIPThrottle, api.MobileAuthDeviceThrottle], name)
                self.assertTrue(cls.throttle_scope, name)
            else:
                self.assertEqual(throttles, [api.MobileUserThrottle], name)
        self.assertEqual(api.MobileUserThrottle.scope, 'mobile')

    def test_default_rate_is_1200_an_hour(self):
        from django.conf import settings
        self.assertEqual(settings.MOBILE_API_THROTTLE_RATE, '1200/hour')
        self.assertEqual(api.MobileUserThrottle().rate, '1200/hour')

    @override_settings(MOBILE_API_THROTTLE_RATE='3/min')
    def test_rate_comes_from_the_setting_and_is_per_member(self):
        codes = [self.api.get('/api/mobile/v1/me/', **bearer(self.access)).status_code for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])
        r = self.api.get('/api/mobile/v1/devices/', **bearer(self.access))
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()['error'], 'throttled')
        self.assertGreater(r.json()['retry_after'], 0)
        # A second device of the SAME member shares the bucket...
        _, access2, _ = make_device(self.user, name='Tablet')
        self.assertEqual(self.api.get('/api/mobile/v1/me/', **bearer(access2)).status_code, 429)
        # ...another member does not.
        _, other, _ = make_device(make_user('thr-2'))
        self.assertEqual(self.api.get('/api/mobile/v1/me/', **bearer(other)).status_code, 200)

    @override_settings(REST_FRAMEWORK={'DEFAULT_THROTTLE_RATES': {'user': '1/hour'},
                                       'DEFAULT_THROTTLE_CLASSES': [
                                           'rest_framework.throttling.UserRateThrottle']})
    def test_site_wide_user_throttle_does_not_apply(self):
        for _ in range(3):
            self.assertEqual(self.api.get('/api/mobile/v1/me/', **bearer(self.access)).status_code, 200)

    @override_settings(MOBILE_API_THROTTLE_RATE='1000/min')
    def test_pre_auth_throttles_stay_strict(self):
        statuses = [self.api.post('/api/mobile/v1/auth/login/', {},
                                  content_type='application/json').status_code for _ in range(11)]
        self.assertEqual(statuses[-1], 429)


class MeLinksTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = base_make_user('ml-1')
        self.access = self.login(self.user.username).json()['access_token']
        self.edu = Committee.objects.create(name='Education', code='EDU', is_education_committee=True)

    def links(self):
        r = self.get_me(self.access)
        self.assertEqual(r.status_code, 200)
        return r.json()['links']

    def test_null_for_a_non_chair(self):
        self.assertEqual(self.links(), {'education': None})

    def test_path_for_the_education_chair(self):
        self.edu.chairs.add(self.user)
        self.assertEqual(self.links(), {'education': '/committee/EDU/education/'})

    def test_same_answer_as_the_home_page_card(self):
        from src.view.home import committee_dashboard_links
        self.edu.chairs.add(self.user)
        card = [l['url'] for l in committee_dashboard_links(self.user) if l['label'] == 'Education']
        self.assertEqual(card, [self.links()['education']])

    def test_null_when_committee_pages_are_off_or_committee_inactive(self):
        self.edu.chairs.add(self.user)
        PageToggle.objects.create(url_name='committee_home', display_name='Committees', is_enabled=False)
        self.assertIsNone(self.links()['education'])
        PageToggle.objects.filter(url_name='committee_home').update(is_enabled=True)
        cache.clear()
        Committee.objects.filter(pk=self.edu.pk).update(is_active=False)
        self.assertIsNone(self.links()['education'])


class VerifyLockoutKeepsTicketTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        self.user = base_make_user('vl-1')
        self.otp = totp_device(self.user)

    def test_locked_ip_does_not_consume_the_ticket(self):
        ticket = self.login(self.user.username).json()['ticket']
        until = timezone.now() + timedelta(minutes=5)
        cache.set(lp.get_lockout_key('127.0.0.1'), until, 300)
        r = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.otp)})
        self.assertEqual(r.status_code, 423)
        self.assertEqual(r.json()['error'], 'locked')
        self.assertTrue(MobileAuthTicket.objects.filter(used_at__isnull=True).exists())
        self.assertIsNone(cache.get(f'2fa_failures_{self.user.pk}'))   # not counted as a wrong code
        cache.delete(lp.get_lockout_key('127.0.0.1'))
        r = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(self.otp)})
        self.assertEqual(r.status_code, 200, r.content)


class WellKnownMultipleIdsTests(TestCase):
    CONFIG = dict(
        MOBILE_IOS_TEAM_ID='ABCDE12345',
        MOBILE_IOS_BUNDLE_ID='org.amparliament.app, org.amparliament.app.development,'
                             'org.amparliament.app.preview,',
        MOBILE_ANDROID_PACKAGE='org.amparliament.app,org.amparliament.app.preview',
        MOBILE_ANDROID_CERT_SHA256=['AA:BB', 'CC:DD'],
    )

    def test_aasa_lists_every_app_id(self):
        with self.settings(**self.CONFIG):
            body = json.loads(Client().get('/.well-known/apple-app-site-association').content)
        ids = ['ABCDE12345.org.amparliament.app', 'ABCDE12345.org.amparliament.app.development',
               'ABCDE12345.org.amparliament.app.preview']
        self.assertEqual(body['webcredentials'], {'apps': ids})
        self.assertEqual(len(body['applinks']['details']), 1)
        self.assertEqual(body['applinks']['details'][0]['appIDs'], ids)

    def test_assetlinks_one_statement_per_package_sharing_fingerprints(self):
        with self.settings(**self.CONFIG):
            body = json.loads(Client().get('/.well-known/assetlinks.json').content)
        self.assertEqual([s['target']['package_name'] for s in body],
                         ['org.amparliament.app', 'org.amparliament.app.preview'])
        for statement in body:
            self.assertEqual(statement['target']['sha256_cert_fingerprints'], ['AA:BB', 'CC:DD'])
            self.assertEqual(statement['relation'], [
                'delegate_permission/common.handle_all_urls',
                'delegate_permission/common.get_login_creds'])

    def test_blank_list_is_unconfigured(self):
        with self.settings(MOBILE_IOS_TEAM_ID='T', MOBILE_IOS_BUNDLE_ID=' , ',
                           MOBILE_ANDROID_PACKAGE=',', MOBILE_ANDROID_CERT_SHA256=['AA']):
            self.assertEqual(Client().get('/.well-known/apple-app-site-association').status_code, 404)
            self.assertEqual(Client().get('/.well-known/assetlinks.json').status_code, 404)

    def test_duplicates_collapse(self):
        from src.mobile.views_well_known import setting_list
        with self.settings(MOBILE_ANDROID_PACKAGE='a.b, a.b ,c.d'):
            self.assertEqual(setting_list('MOBILE_ANDROID_PACKAGE'), ['a.b', 'c.d'])
        with self.settings(MOBILE_ANDROID_PACKAGE=['a.b', 'c.d']):
            self.assertEqual(setting_list('MOBILE_ANDROID_PACKAGE'), ['a.b', 'c.d'])
