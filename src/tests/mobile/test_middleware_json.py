"""
/api/mobile/ gets JSON from the site-wide middlewares, and Enforce2FAMiddleware
is NOT prefix-exempted: a Bearer request passes it only because it carries no
session user.
"""
from django.urls import reverse
from django.utils import timezone

from src.models import SystemLockdown
from src.models_feature_flags import FeatureFlag
from src.tests.mobile.base import MobileTestCase, make_user, totp_code, totp_device


class MaintenanceAndLockdownTests(MobileTestCase):

    def test_maintenance_is_json_503(self):
        user = make_user('m500')
        FeatureFlag.objects.create(name='maintenance_mode', display_name='M', description='x',
                                   is_enabled=True)
        r = self.login(user.username)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertEqual(r.json(), {'error': 'maintenance'})

    def test_maintenance_lets_a_bearer_admin_through(self):
        admin = make_user('m501', is_admin=True)
        member = make_user('m502')
        admin_access = self.login(admin.username).json()['access_token']
        member_access = self.login(member.username).json()['access_token']
        FeatureFlag.objects.create(name='maintenance_mode', display_name='M', description='x',
                                   is_enabled=True)
        self.assertEqual(self.get_me(admin_access).status_code, 200)
        self.assertEqual(self.get_me(member_access).json(), {'error': 'maintenance'})

    def test_lockdown_is_json_503(self):
        lock = SystemLockdown.get_instance()
        lock.is_active = True
        lock.reason = 'drill'
        lock.message = 'Back soon'
        lock.activated_at = timezone.now()
        lock.save()
        r = self.post('auth/login/', {})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {'error': 'lockdown', 'message': 'Back soon'})


class AccountStateTests(MobileTestCase):

    def test_forced_password_change_is_json_403_but_logout_works(self):
        user = make_user('m510')
        pair = self.login(user.username).json()
        type(user).objects.filter(pk=user.pk).update(force_password_change=True)
        r = self.get_me(pair['access_token'])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {'error': 'password_change_required'})
        r = self.api.post('/api/mobile/v1/auth/logout/', **self.bearer(pair['access_token']))
        self.assertEqual(r.status_code, 200)

    def test_quarantined_session_on_mobile_path_is_json(self):
        user = make_user('m511', is_quarantined=True)
        self.api.force_login(user)
        r = self.api.get('/api/mobile/v1/me/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {'error': 'quarantined'})

    def test_pledge_restriction_is_json(self):
        from unittest.mock import patch
        pledge = make_user('m512', member_type='Pledge')
        access = self.login(pledge.username).json()['access_token']
        # `mobile_me` is always allowed; pretend a restricted mobile page:
        with patch('src.middleware.pledge_pages.is_always_allowed', return_value=False), \
             patch('src.models.education.PledgePageRestriction.is_allowed', return_value=False):
            r = self.get_me(access)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json(), {'error': 'pledge_restricted'})
        self.assertEqual(self.get_me(access).status_code, 200)


class Enforce2FAIsNotBypassedTests(MobileTestCase):

    def test_bearer_passes_while_a_password_only_session_is_bounced(self):
        user = make_user('m520')
        device = totp_device(user)
        ticket = self.login(user.username).json()['ticket']
        access = self.post('auth/verify/', {'ticket': ticket, 'code': totp_code(device)}).json()['access_token']

        # The app: Bearer, no session → the middleware sees no user, DRF authenticates.
        self.assertEqual(self.get_me(access).status_code, 200)

        # A password-only (2FA-unverified) SESSION is still sent to verify on
        # web pages...
        from django.test import Client
        web = Client()
        web.force_login(user)
        r = web.get(reverse('home'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse('two_factor_verify'), r['Location'])
        # ...and on /api/mobile/ too: there is no prefix exemption, and a session
        # alone never authenticates a mobile view.
        r = web.get('/api/mobile/v1/me/')
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse('two_factor_verify'), r['Location'])
        r = web.get('/api/mobile/v1/me/', HTTP_AUTHORIZATION=f'Bearer {access}')
        self.assertEqual(r.status_code, 302)

    def test_a_verified_session_without_bearer_is_not_a_mobile_login(self):
        user = make_user('m521')
        self.api.force_login(user)   # no 2FA device → middleware passes it
        r = self.api.get('/api/mobile/v1/me/')
        self.assertEqual(r.status_code, 401)
