"""
Comprehensive test suite for Two-Factor Authentication functionality.
Tests 2FA setup, verification, middleware enforcement, and admin dashboard.

Run with: python manage.py test src.test_two_factor
"""

import time
import hmac
import hashlib
import struct

from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp import user_has_device

from .models import ParliamentUser, TwoFactorRequirement
from .models_feature_flags import SiteSetting
from .middleware.two_factor import Enforce2FAMiddleware


def generate_totp(device, interval=30, digits=6):
    """Generate a valid TOTP token for a device"""
    counter = int(time.time() // interval)
    msg = struct.pack('>Q', counter)
    h = hmac.new(device.bin_key, msg, hashlib.sha1).digest()
    o = h[19] & 15
    code = (struct.unpack('>I', h[o:o+4])[0] & 0x7fffffff) % (10 ** digits)
    return str(code).zfill(digits)


class TwoFactorSetupTestCase(TestCase):
    """Test 2FA setup flow"""

    def setUp(self):
        """Set up test user and client"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='testuser1',
            name='Test User',
            username='testuser',
            member_type='Member'
        )
        self.user.set_password('testpass123')
        self.user.email = 'testuser@parliament.test'
        self.user.save()
        self.client.force_login(self.user)

    def test_setup_page_accessible(self):
        """Test that 2FA setup page loads for logged in user"""
        response = self.client.get(reverse('two_factor_setup'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'two_factor/setup.html')

    def test_setup_page_requires_login(self):
        """Test that 2FA setup page requires authentication"""
        self.client.logout()
        response = self.client.get(reverse('two_factor_setup'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_setup_creates_unconfirmed_device(self):
        """Test that visiting setup page creates an unconfirmed TOTP device"""
        response = self.client.get(reverse('two_factor_setup'))
        self.assertEqual(response.status_code, 200)

        # Check device was created
        device = TOTPDevice.objects.filter(user=self.user, confirmed=False).first()
        self.assertIsNotNone(device)
        self.assertEqual(device.name, 'default')
        self.assertFalse(device.confirmed)

    def test_setup_context_contains_required_data(self):
        """Test that setup page context has QR code URL and manual key"""
        response = self.client.get(reverse('two_factor_setup'))

        self.assertIn('qr_code_url', response.context)
        self.assertIn('manual_entry_key', response.context)
        self.assertIn('account_name', response.context)
        self.assertEqual(response.context['account_name'], self.user.username)

    def test_setup_redirects_if_already_has_2fa(self):
        """Test that user with 2FA enabled is redirected from setup page"""
        # Create confirmed device
        TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=True
        )

        response = self.client.get(reverse('two_factor_setup'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

    def test_setup_verifies_valid_token(self):
        """Test that valid TOTP token confirms the device"""
        # First visit setup to create device
        self.client.get(reverse('two_factor_setup'))
        device = TOTPDevice.objects.filter(user=self.user, confirmed=False).first()

        # Generate a valid token
        valid_token = generate_totp(device)

        response = self.client.post(reverse('two_factor_setup'), {
            'token': valid_token
        })

        # Device should now be confirmed
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_backup_codes_reveal'))

    def test_setup_rejects_invalid_token(self):
        """Test that invalid TOTP token is rejected"""
        # First visit setup to create device
        self.client.get(reverse('two_factor_setup'))

        response = self.client.post(reverse('two_factor_setup'), {
            'token': '000000'
        })

        # Device should remain unconfirmed
        device = TOTPDevice.objects.filter(user=self.user).first()
        self.assertFalse(device.confirmed)
        self.assertEqual(response.status_code, 200)


class TwoFactorQRCodeTestCase(TestCase):
    """Test QR code generation"""

    def setUp(self):
        """Set up test user and client"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='qruser1',
            name='QR Test User',
            username='qruser',
            member_type='Member'
        )
        self.user.set_password('testpass123')
        self.user.email = 'qruser@parliament.test'
        self.user.save()
        self.client.force_login(self.user)

    def test_qrcode_returns_svg(self):
        """Test that QR code endpoint returns SVG image"""
        # First create a device
        self.client.get(reverse('two_factor_setup'))

        response = self.client.get(reverse('two_factor_qrcode'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/svg+xml')

    def test_qrcode_404_without_device(self):
        """Test that QR code endpoint returns 404 without unconfirmed device"""
        response = self.client.get(reverse('two_factor_qrcode'))
        self.assertEqual(response.status_code, 404)


class TwoFactorVerifyTestCase(TestCase):
    """Test 2FA verification during login"""

    def setUp(self):
        """Set up test user with 2FA enabled"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='verifyuser1',
            name='Verify Test User',
            username='verifyuser',
            member_type='Member'
        )
        self.user.set_password('testpass123')
        self.user.save()

        # Create confirmed 2FA device
        self.device = TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=True
        )

        self.client.force_login(self.user)

    def test_verify_page_accessible(self):
        """Test that verify page loads for user with 2FA"""
        response = self.client.get(reverse('two_factor_verify'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'two_factor/verify.html')

    def test_verify_redirects_without_2fa(self):
        """Test that user without 2FA is redirected to setup"""
        # Delete the device
        self.device.delete()

        response = self.client.get(reverse('two_factor_verify'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_setup'))

    def test_verify_accepts_valid_token(self):
        """Test that valid token verifies the session"""
        valid_token = generate_totp(self.device)

        response = self.client.post(reverse('two_factor_verify'), {
            'token': valid_token
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

        # Verify that accessing the verify page again redirects to home (user is now verified)
        response = self.client.get(reverse('two_factor_verify'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

    def test_verify_rejects_invalid_token(self):
        """Test that invalid token is rejected"""
        response = self.client.post(reverse('two_factor_verify'), {
            'token': '000000'
        })

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_otp_device_id', self.client.session)


class TwoFactorDisableTestCase(TestCase):
    """Test 2FA disable functionality"""

    def setUp(self):
        """Set up test user with 2FA enabled"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='disableuser1',
            name='Disable Test User',
            username='disableuser',
            member_type='Member'
        )
        self.user.set_password('testpass123')
        self.user.save()

        # Create confirmed 2FA device
        self.device = TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=True
        )

        self.client.force_login(self.user)

    def test_disable_page_accessible(self):
        """Test that disable page loads"""
        response = self.client.get(reverse('two_factor_disable'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'two_factor/disable.html')

    def test_disable_removes_devices(self):
        """Test that POST request removes all 2FA devices"""
        # Verify device exists
        self.assertTrue(user_has_device(self.user))

        response = self.client.post(reverse('two_factor_disable'))

        # Device should be deleted
        self.assertFalse(user_has_device(self.user))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('profile'))


class TwoFactorMiddlewareTestCase(TestCase):
    """Test 2FA middleware enforcement"""

    def setUp(self):
        """Set up test users and middleware"""
        self.factory = RequestFactory()
        self.middleware = Enforce2FAMiddleware(lambda r: MagicMock(status_code=200))

        # Create different user types
        self.admin_user = ParliamentUser.objects.create_user(
            user_id='adminuser1',
            name='Admin User',
            username='admin',
            member_type='Officer'
        )
        self.admin_user.is_admin = True
        self.admin_user.save()

        self.officer_user = ParliamentUser.objects.create_user(
            user_id='officeruser1',
            name='Officer User',
            username='officer',
            member_type='Officer'
        )

        self.member_user = ParliamentUser.objects.create_user(
            user_id='memberuser1',
            name='Member User',
            username='member',
            member_type='Member'
        )

    def test_unauthenticated_user_allowed(self):
        """Test that unauthenticated users pass through"""
        request = self.factory.get('/home/')
        request.user = MagicMock(is_authenticated=False)

        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_exempt_paths_allowed(self):
        """Test that exempt paths are not blocked"""
        exempt_paths = ['/login/', '/logout/', '/accounts/two-factor/setup/', '/static/test.css']

        for path in exempt_paths:
            request = self.factory.get(path)
            request.user = self.member_user

            response = self.middleware(request)
            # Should not redirect
            self.assertNotEqual(getattr(response, 'url', None), reverse('two_factor_setup'))

    def test_policy_none_allows_all(self):
        """Test that 'none' policy allows all users without 2FA"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'none', 'setting_type': 'string'}
        )

        request = self.factory.get('/home/')
        request.user = self.admin_user

        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_policy_admins_only_requires_admin_2fa(self):
        """Test that 'admins_only' policy requires 2FA for admins"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'admins_only', 'setting_type': 'string'}
        )

        request = self.factory.get('/home/')
        request.user = self.admin_user

        response = self.middleware(request)
        # Admin without 2FA should be redirected to setup
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_setup'))

    def test_policy_admins_only_allows_members(self):
        """Test that 'admins_only' policy allows regular members"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'admins_only', 'setting_type': 'string'}
        )

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_policy_officers_and_admins(self):
        """Test that 'officers_and_admins' policy requires 2FA for officers"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'officers_and_admins', 'setting_type': 'string'}
        )

        request = self.factory.get('/home/')
        request.user = self.officer_user

        response = self.middleware(request)
        # Officer without 2FA should be redirected
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_setup'))

    def test_policy_all_members(self):
        """Test that 'all_members' policy requires 2FA for everyone"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'all_members', 'setting_type': 'string'}
        )

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        # Regular member without 2FA should be redirected
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_setup'))

    def test_individual_requirement_overrides_policy(self):
        """Test that individual 'required' overrides 'none' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'none', 'setting_type': 'string'}
        )

        # Set individual requirement
        TwoFactorRequirement.objects.create(
            user=self.member_user,
            requirement='required'
        )

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        # Should be redirected despite 'none' policy
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_setup'))

    def test_individual_exempt_overrides_policy(self):
        """Test that individual 'exempt' overrides 'all_members' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'all_members', 'setting_type': 'string'}
        )

        # Set individual exemption
        TwoFactorRequirement.objects.create(
            user=self.member_user,
            requirement='exempt'
        )

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        # Should pass despite 'all_members' policy
        self.assertEqual(response.status_code, 200)

    def test_user_with_2fa_verified_allowed(self):
        """Test that user with verified 2FA is allowed"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'all_members', 'setting_type': 'string'}
        )

        # Create verified device
        TOTPDevice.objects.create(
            user=self.member_user,
            name='default',
            confirmed=True
        )

        # Mock is_verified to return True
        self.member_user.is_verified = MagicMock(return_value=True)

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        self.assertEqual(response.status_code, 200)

    def test_user_with_2fa_unverified_redirects_to_verify(self):
        """Test that user with 2FA but unverified session redirects to verify"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'all_members', 'setting_type': 'string'}
        )

        # Create confirmed device
        TOTPDevice.objects.create(
            user=self.member_user,
            name='default',
            confirmed=True
        )

        # Mock is_verified to return False
        self.member_user.is_verified = MagicMock(return_value=False)

        request = self.factory.get('/home/')
        request.user = self.member_user

        response = self.middleware(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('two_factor_verify'))


class TwoFactorUserRequiresMethodTestCase(TestCase):
    """Test the user_requires_2fa method directly"""

    def setUp(self):
        """Set up test users"""
        self.middleware = Enforce2FAMiddleware(lambda r: None)

        self.admin_user = ParliamentUser.objects.create_user(
            user_id='admin2',
            name='Admin User 2',
            username='admin2',
            member_type='Officer'
        )
        self.admin_user.is_admin = True
        self.admin_user.save()

        self.officer_user = ParliamentUser.objects.create_user(
            user_id='officer2',
            name='Officer User 2',
            username='officer2',
            member_type='Officer'
        )

        self.member_user = ParliamentUser.objects.create_user(
            user_id='member2',
            name='Member User 2',
            username='member2',
            member_type='Member'
        )

    def test_requires_2fa_none_policy(self):
        """Test user_requires_2fa with 'none' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'none', 'setting_type': 'string'}
        )

        self.assertFalse(self.middleware.user_requires_2fa(self.admin_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.officer_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.member_user))

    def test_requires_2fa_admins_only_policy(self):
        """Test user_requires_2fa with 'admins_only' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'admins_only', 'setting_type': 'string'}
        )

        self.assertTrue(self.middleware.user_requires_2fa(self.admin_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.officer_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.member_user))

    def test_requires_2fa_officers_and_admins_policy(self):
        """Test user_requires_2fa with 'officers_and_admins' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'officers_and_admins', 'setting_type': 'string'}
        )

        self.assertTrue(self.middleware.user_requires_2fa(self.admin_user))
        self.assertTrue(self.middleware.user_requires_2fa(self.officer_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.member_user))

    def test_requires_2fa_all_members_policy(self):
        """Test user_requires_2fa with 'all_members' policy"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'all_members', 'setting_type': 'string'}
        )

        self.assertTrue(self.middleware.user_requires_2fa(self.admin_user))
        self.assertTrue(self.middleware.user_requires_2fa(self.officer_user))
        self.assertTrue(self.middleware.user_requires_2fa(self.member_user))

    def test_requires_2fa_custom_policy_without_requirement(self):
        """Test user_requires_2fa with 'custom' policy and no individual requirement"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'custom', 'setting_type': 'string'}
        )

        self.assertFalse(self.middleware.user_requires_2fa(self.admin_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.officer_user))
        self.assertFalse(self.middleware.user_requires_2fa(self.member_user))

    def test_requires_2fa_custom_policy_with_required(self):
        """Test user_requires_2fa with 'custom' policy and 'required' setting"""
        SiteSetting.objects.update_or_create(
            key='2fa_policy_mode',
            defaults={'value': 'custom', 'setting_type': 'string'}
        )

        TwoFactorRequirement.objects.create(
            user=self.member_user,
            requirement='required'
        )

        self.assertFalse(self.middleware.user_requires_2fa(self.admin_user))
        self.assertTrue(self.middleware.user_requires_2fa(self.member_user))


class TwoFactorRequirementModelTestCase(TestCase):
    """Test TwoFactorRequirement model"""

    def setUp(self):
        """Set up test users"""
        self.user = ParliamentUser.objects.create_user(
            user_id='requser1',
            name='Requirement User',
            username='requser',
            member_type='Member'
        )

        self.admin = ParliamentUser.objects.create_user(
            user_id='setbyadmin1',
            name='Set By Admin',
            username='setby',
            member_type='Officer'
        )
        self.admin.is_admin = True
        self.admin.save()

    def test_create_requirement(self):
        """Test creating a TwoFactorRequirement"""
        req = TwoFactorRequirement.objects.create(
            user=self.user,
            requirement='required',
            reason='Security policy',
            set_by=self.admin
        )

        self.assertEqual(req.user, self.user)
        self.assertEqual(req.requirement, 'required')
        self.assertEqual(req.reason, 'Security policy')
        self.assertEqual(req.set_by, self.admin)

    def test_requirement_choices(self):
        """Test that requirement field only accepts valid choices"""
        # Valid choices
        for choice in ['required', 'exempt']:
            req = TwoFactorRequirement(user=self.user, requirement=choice)
            req.full_clean()  # Should not raise

    def test_one_requirement_per_user(self):
        """Test that only one requirement can exist per user"""
        TwoFactorRequirement.objects.create(
            user=self.user,
            requirement='required'
        )

        # Trying to create another should fail
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            TwoFactorRequirement.objects.create(
                user=self.user,
                requirement='exempt'
            )

    def test_user_relation(self):
        """Test accessing requirement from user"""
        req = TwoFactorRequirement.objects.create(
            user=self.user,
            requirement='required'
        )

        self.assertEqual(self.user.two_factor_requirement, req)


class TwoFactorProfileIntegrationTestCase(TestCase):
    """Test 2FA integration in profile page"""

    def setUp(self):
        """Set up test user and client"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='profileuser1',
            name='Profile User',
            username='profileuser',
            member_type='Member'
        )
        self.user.set_password('testpass123')
        self.user.save()
        self.client.force_login(self.user)

    def test_profile_shows_2fa_disabled(self):
        """Test that profile page shows 2FA as disabled when not set up"""
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)

        # Check context
        self.assertIn('has_2fa', response.context)
        self.assertFalse(response.context['has_2fa'])

    def test_profile_shows_2fa_enabled(self):
        """Test that profile page shows 2FA as enabled when set up"""
        # Create confirmed device
        TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=True
        )

        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)

        # Check context
        self.assertIn('has_2fa', response.context)
        self.assertTrue(response.context['has_2fa'])


class TwoFactorAdminDashboardTestCase(TestCase):
    """Test admin 2FA dashboard views"""

    def setUp(self):
        """Set up admin user and client"""
        self.client = Client()

        # Create admin user - must use user_id='73' for admin_v2 access
        self.admin = ParliamentUser.objects.create_user(
            user_id='73',
            name='Dashboard Admin',
            username='dashboardadmin',
            member_type='Officer'
        )
        self.admin.is_admin = True
        self.admin.set_password('testpass123')
        self.admin.save()

        # Create some test members - use numeric user_ids for ActivityLog compatibility
        self.member1 = ParliamentUser.objects.create_user(
            user_id='101',
            name='Dashboard User 1',
            username='dashuser1',
            member_type='Member'
        )

        self.member2 = ParliamentUser.objects.create_user(
            user_id='102',
            name='Dashboard User 2',
            username='dashuser2',
            member_type='Member'
        )

    def _login_admin_v2(self):
        """Helper to login and set admin v2 session"""
        from django.utils import timezone
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def test_dashboard_requires_admin_v2_auth(self):
        """Test that dashboard requires admin-v2 authentication"""
        self.client.force_login(self.admin)

        response = self.client.get(reverse('admin_v2_two_factor'))
        # Should redirect to admin-v2 login
        self.assertEqual(response.status_code, 302)

    def test_dashboard_accessible_with_auth(self):
        """Test that dashboard is accessible with proper auth"""
        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'admin_v2/two_factor_dashboard.html')

    def test_dashboard_shows_members(self):
        """Test that dashboard shows member list"""
        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor'))
        self.assertEqual(response.status_code, 200)

        self.assertIn('member_data', response.context)

    def test_dashboard_shows_stats(self):
        """Test that dashboard shows statistics"""
        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor'))
        self.assertEqual(response.status_code, 200)

        self.assertIn('stats', response.context)
        stats = response.context['stats']
        self.assertIn('total', stats)
        self.assertIn('has_2fa', stats)

    def test_update_policy(self):
        """Test updating 2FA policy"""
        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_update_policy'),
            data='{"policy": "admins_only"}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['policy'], 'admins_only')

        # Verify setting was saved
        policy = SiteSetting.get_setting('2fa_policy_mode', 'none')
        self.assertEqual(policy, 'admins_only')

    def test_update_policy_invalid(self):
        """Test updating 2FA policy with invalid value"""
        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_update_policy'),
            data='{"policy": "invalid_policy"}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_set_individual_requirement(self):
        """Test setting individual 2FA requirement"""
        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_set_requirement', args=[self.member1.user_id]),
            data='{"requirement": "required", "reason": "Test requirement"}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # Verify requirement was saved
        req = TwoFactorRequirement.objects.get(user=self.member1)
        self.assertEqual(req.requirement, 'required')
        self.assertEqual(req.reason, 'Test requirement')

    def test_clear_individual_requirement(self):
        """Test clearing individual 2FA requirement"""
        # First create a requirement
        TwoFactorRequirement.objects.create(
            user=self.member1,
            requirement='required'
        )

        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_set_requirement', args=[self.member1.user_id]),
            data='{"requirement": "clear"}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # Verify requirement was deleted
        self.assertFalse(TwoFactorRequirement.objects.filter(user=self.member1).exists())

    def test_bulk_require_action(self):
        """Test bulk require 2FA action"""
        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_bulk_action'),
            data=f'{{"action": "require", "user_ids": ["{self.member1.user_id}", "{self.member2.user_id}"]}}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['count'], 2)

        # Verify requirements were created
        self.assertEqual(
            TwoFactorRequirement.objects.filter(user__in=[self.member1, self.member2]).count(),
            2
        )

    def test_bulk_exempt_action(self):
        """Test bulk exempt from 2FA action"""
        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_bulk_action'),
            data=f'{{"action": "exempt", "user_ids": ["{self.member1.user_id}"]}}',
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # Verify exemption was created
        req = TwoFactorRequirement.objects.get(user=self.member1)
        self.assertEqual(req.requirement, 'exempt')

    def test_reset_user_2fa(self):
        """Test admin resetting user's 2FA"""
        # Create device for member
        TOTPDevice.objects.create(
            user=self.member1,
            name='default',
            confirmed=True
        )

        self.assertTrue(user_has_device(self.member1))

        self._login_admin_v2()

        response = self.client.post(
            reverse('admin_v2_two_factor_reset', args=[self.member1.user_id])
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # Verify device was deleted
        self.assertFalse(user_has_device(self.member1))

    def test_dashboard_filter_has_2fa(self):
        """Test filtering members who have 2FA enabled"""
        # Create device for member1
        TOTPDevice.objects.create(
            user=self.member1,
            name='default',
            confirmed=True
        )

        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor') + '?filter=has_2fa')
        self.assertEqual(response.status_code, 200)

        member_data = response.context['member_data']
        # Should only include member1 (who has 2FA)
        member_ids = [m['user'].user_id for m in member_data]
        self.assertIn(self.member1.user_id, member_ids)

    def test_dashboard_filter_no_2fa(self):
        """Test filtering members who don't have 2FA"""
        # Create device for member1
        TOTPDevice.objects.create(
            user=self.member1,
            name='default',
            confirmed=True
        )

        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor') + '?filter=no_2fa')
        self.assertEqual(response.status_code, 200)

        member_data = response.context['member_data']
        # Should not include member1 (who has 2FA)
        member_ids = [m['user'].user_id for m in member_data]
        self.assertNotIn(self.member1.user_id, member_ids)

    def test_dashboard_search(self):
        """Test searching members by name"""
        self._login_admin_v2()

        response = self.client.get(reverse('admin_v2_two_factor') + '?search=User%201')
        self.assertEqual(response.status_code, 200)

        member_data = response.context['member_data']
        # Should only include member1
        member_ids = [m['user'].user_id for m in member_data]
        self.assertIn(self.member1.user_id, member_ids)
        self.assertNotIn(self.member2.user_id, member_ids)
