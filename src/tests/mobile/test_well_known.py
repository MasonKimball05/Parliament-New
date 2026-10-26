"""
Mobile plan, Phase 1 — /.well-known/apple-app-site-association and
/.well-known/assetlinks.json: 404 until configured, exact JSON when set, no
redirect, and reachable by a signed-in but 2FA-unverified session, in
maintenance mode and during a lockdown.
"""
import json

from django.test import Client, TestCase, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import ParliamentUser

AASA = '/.well-known/apple-app-site-association'
ASSETLINKS = '/.well-known/assetlinks.json'
CONFIGURED = dict(
    MOBILE_IOS_TEAM_ID='ABCDE12345', MOBILE_IOS_BUNDLE_ID='org.amparliament.app',
    MOBILE_ANDROID_PACKAGE='org.amparliament.app',
    MOBILE_ANDROID_CERT_SHA256=['AA:BB', 'CC:DD'],
)


@override_settings(MOBILE_IOS_TEAM_ID='', MOBILE_IOS_BUNDLE_ID='',
                   MOBILE_ANDROID_PACKAGE='', MOBILE_ANDROID_CERT_SHA256=[])
class UnconfiguredTests(TestCase):
    def test_404_when_unset(self):
        for url in (AASA, ASSETLINKS):
            self.assertEqual(Client().get(url).status_code, 404, url)

    @override_settings(MOBILE_IOS_TEAM_ID='ABCDE12345')
    def test_404_when_half_set(self):
        self.assertEqual(Client().get(AASA).status_code, 404)


@override_settings(**CONFIGURED)
class ConfiguredTests(TestCase):
    def test_aasa(self):
        r = Client().get(AASA)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/json')
        body = json.loads(r.content)
        app_id = 'ABCDE12345.org.amparliament.app'
        self.assertEqual(body['webcredentials'], {'apps': [app_id]})
        details = body['applinks']['details']
        self.assertEqual(len(details), 1)
        self.assertEqual(details[0]['appIDs'], [app_id])
        comps = details[0]['components']
        excluded = [c['/'] for c in comps if c.get('exclude')]
        self.assertEqual(excluded, ['/m/handoff/*', '/admin*', '/api/*', '/logout/*'])
        self.assertEqual(comps[-1], {'/': '*'})   # excludes must come first

    def test_assetlinks(self):
        r = Client().get(ASSETLINKS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertEqual(json.loads(r.content), [{
            'relation': ['delegate_permission/common.handle_all_urls',
                         'delegate_permission/common.get_login_creds'],
            'target': {'namespace': 'android_app', 'package_name': 'org.amparliament.app',
                       'sha256_cert_fingerprints': ['AA:BB', 'CC:DD']},
        }])

    def test_no_redirect_for_slash_variant_or_follow(self):
        # The exact paths answer directly; nothing appends a slash.
        for url in (AASA, ASSETLINKS):
            r = Client().get(url, follow=False)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.has_header('Location'))

    def test_not_2fa_gated(self):
        user = ParliamentUser.objects.create(
            user_id='wk-2fa', username='wk_2fa', name='WK', member_type='Member',
            member_status='Active')
        TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        client = Client()
        client.force_login(user)  # password only: not OTP-verified
        self.assertEqual(client.get('/home/').status_code, 302)  # control: gated
        for url in (AASA, ASSETLINKS):
            self.assertEqual(client.get(url).status_code, 200, url)

    def test_reachable_in_maintenance_mode(self):
        from src.models_feature_flags import FeatureFlag
        FeatureFlag.objects.update_or_create(name='maintenance_mode', defaults={'is_enabled': True})
        for url in (AASA, ASSETLINKS):
            self.assertEqual(Client().get(url).status_code, 200, url)

    def test_exempt_from_lockdown_and_pledge_gates(self):
        from src.middleware.lockdown import EmergencyLockdownMiddleware
        from src.middleware.pledge_pages import is_always_allowed
        mw = EmergencyLockdownMiddleware(lambda r: None)
        self.assertTrue(any(AASA.startswith(p) for p in mw.always_allowed))
        self.assertTrue(is_always_allowed('apple_app_site_association'))
        self.assertTrue(is_always_allowed('assetlinks'))

    def test_not_a_honeypot(self):
        from django.urls import resolve
        for url in (AASA, ASSETLINKS):
            self.assertFalse(resolve(url).url_name.startswith('honeypot'))
