"""
Tests for admin login-as-user (impersonation) flow.

Run with: python manage.py test src.test_login_as
"""

from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock

from src.models import ParliamentUser
from src.middleware.two_factor import Enforce2FAMiddleware
from src.context_processors import impersonation as impersonation_processor
from src.view.login_as_view import SESSION_ORIGINAL_ID, SESSION_ORIGINAL_NAME
from src.models_feature_flags import FeatureFlag

ParliamentUser = get_user_model()


def make_user(username, user_id, is_admin_flag=False):
    u = ParliamentUser.objects.create_user(
        username=username,
        user_id=user_id,
        name=username.capitalize(),
        member_type='Officer' if is_admin_flag else 'Member',
        password='TestPass123!',
    )
    if is_admin_flag:
        u.is_admin = True
        u.save(update_fields=['is_admin'])
    return u


class LoginAsViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = make_user('admin_user', 'ADM01', is_admin_flag=True)
        self.target = make_user('target_user', 'TGT01')
        # v3.29.35 — 'login_as_user' is DISABLED_BY_DEFAULT (see
        # src/models_feature_flags.py). This class tests the impersonation
        # flow itself, so it turns the flag on here, the way a chapter that
        # actually wants the feature would. `LoginAsUserFeatureFlagTests`
        # below covers the flag OFF (and unseeded) cases specifically.
        FeatureFlag.objects.create(name='login_as_user', is_enabled=True)

    def _login_as_admin(self):
        self.client.force_login(self.admin)

    # ------------------------------------------------------------------
    # 1. Non-staff users cannot access the view
    # ------------------------------------------------------------------
    def test_non_staff_cannot_impersonate(self):
        self.client.force_login(self.target)
        url = reverse('login-as', args=[self.target.pk])
        response = self.client.get(url)
        # Should redirect to admin login, not allow through
        self.assertNotEqual(response.status_code, 200)
        # Session should NOT have impersonation key
        self.assertNotIn(SESSION_ORIGINAL_ID, self.client.session)

    # ------------------------------------------------------------------
    # 2. Staff admin can log in as another user
    # ------------------------------------------------------------------
    def test_admin_can_impersonate(self):
        self._login_as_admin()
        url = reverse('login-as', args=[self.target.pk])
        response = self.client.get(url)
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        # Now logged in as target
        self.assertEqual(self.client.session['_auth_user_id'], self.target.pk)

    # ------------------------------------------------------------------
    # 3. Session stores original admin info after impersonation starts
    # ------------------------------------------------------------------
    def test_session_stores_original_admin(self):
        self._login_as_admin()
        self.client.get(reverse('login-as', args=[self.target.pk]))
        session = self.client.session
        self.assertIn(SESSION_ORIGINAL_ID, session)
        self.assertEqual(session[SESSION_ORIGINAL_ID], self.admin.user_id)
        self.assertIn(SESSION_ORIGINAL_NAME, session)

    # ------------------------------------------------------------------
    # 4. Context processor exposes is_impersonating = True
    # ------------------------------------------------------------------
    def test_context_processor_is_impersonating(self):
        self._login_as_admin()
        self.client.get(reverse('login-as', args=[self.target.pk]))

        # Make a fake request with the impersonation session
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.target
        request.session = self.client.session

        ctx = impersonation_processor(request)
        self.assertTrue(ctx['is_impersonating'])
        self.assertEqual(ctx['impersonation_original_name'], self.admin.get_display_name())

    # ------------------------------------------------------------------
    # 5. Context processor returns False when not impersonating
    # ------------------------------------------------------------------
    def test_context_processor_not_impersonating(self):
        self._login_as_admin()

        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.admin
        request.session = self.client.session

        ctx = impersonation_processor(request)
        self.assertFalse(ctx['is_impersonating'])

    # ------------------------------------------------------------------
    # 6. Return-to-original logs admin back in and clears session keys
    # ------------------------------------------------------------------
    def test_return_to_original_user(self):
        self._login_as_admin()
        self.client.get(reverse('login-as', args=[self.target.pk]))

        # Confirm we're now the target
        self.assertEqual(self.client.session['_auth_user_id'], self.target.pk)

        # Return
        response = self.client.get(reverse('return_to_original_user'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

        # Confirm back to admin
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

        # Confirm session keys are cleared
        self.assertNotIn(SESSION_ORIGINAL_ID, self.client.session)
        self.assertNotIn(SESSION_ORIGINAL_NAME, self.client.session)

    # ------------------------------------------------------------------
    # 7. Return-to-original with no impersonation session just goes home
    # ------------------------------------------------------------------
    def test_return_without_impersonation_redirects_home(self):
        self._login_as_admin()
        response = self.client.get(reverse('return_to_original_user'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        # Still logged in as admin
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

    # ------------------------------------------------------------------
    # 8. 2FA middleware is bypassed during impersonation
    # ------------------------------------------------------------------
    def test_2fa_bypassed_during_impersonation(self):
        get_response = MagicMock(return_value=MagicMock(status_code=200))
        middleware = Enforce2FAMiddleware(get_response)

        factory = RequestFactory()
        request = factory.get('/some-protected-page/')
        request.user = self.target
        request.session = {SESSION_ORIGINAL_ID: self.admin.user_id}

        with patch.object(middleware, 'user_requires_2fa', return_value=True):
            response = middleware(request)

        # Should pass through — NOT redirect to 2FA
        self.assertEqual(response.status_code, 200)
        get_response.assert_called_once()

    # ------------------------------------------------------------------
    # 9. 2FA middleware still enforces 2FA for normal sessions
    # ------------------------------------------------------------------
    def test_2fa_still_enforced_for_normal_sessions(self):
        get_response = MagicMock(return_value=MagicMock(status_code=200))
        middleware = Enforce2FAMiddleware(get_response)

        factory = RequestFactory()
        request = factory.get('/some-protected-page/')
        request.user = self.target
        request.session = {}  # No impersonation key

        with patch.object(middleware, 'user_requires_2fa', return_value=True), \
             patch('src.middleware.two_factor.user_has_device', return_value=False):
            response = middleware(request)

        # Should redirect to 2FA setup, not call get_response
        get_response.assert_not_called()

    # ------------------------------------------------------------------
    # 10. Impersonation is logged to security logger
    # ------------------------------------------------------------------
    def test_impersonation_logged(self):
        self._login_as_admin()
        with self.assertLogs('security', level='WARNING') as cm:
            self.client.get(reverse('login-as', args=[self.target.pk]))
        self.assertTrue(any('IMPERSONATION START' in line for line in cm.output))

    # ------------------------------------------------------------------
    # 11. Return is logged to security logger
    # ------------------------------------------------------------------
    def test_return_logged(self):
        self._login_as_admin()
        self.client.get(reverse('login-as', args=[self.target.pk]))
        with self.assertLogs('security', level='WARNING') as cm:
            self.client.get(reverse('return_to_original_user'))
        self.assertTrue(any('IMPERSONATION END' in line for line in cm.output))


class LoginAsUserFeatureFlagTests(TestCase):
    """
    v3.29.35 — 'login_as_user' went from an always-live feature (gated only
    by `is_staff`, which on this model is just `is_admin`) to one gated by a
    DISABLED_BY_DEFAULT feature flag. This class is the direct regression
    test for that change: does the flag actually stop both entry points
    (the `/staff/login-as/` view AND the separate `ParliamentUserAdmin`
    method), and does an admin still get back OUT of an impersonation that
    started before the flag was turned off.
    """

    def setUp(self):
        self.client = Client()
        self.admin = make_user('flag_admin', 'FADM1', is_admin_flag=True)
        self.target = make_user('flag_target', 'FTGT1')

    def test_disabled_by_default_with_no_row_blocks_impersonation(self):
        """No FeatureFlag row at all — DISABLED_BY_DEFAULT must fail closed."""
        self.assertFalse(FeatureFlag.objects.filter(name='login_as_user').exists())
        self.client.force_login(self.admin)
        self.client.get(reverse('login-as', args=[self.target.pk]))
        # Still logged in as the admin, not the target.
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

    def test_explicitly_disabled_blocks_impersonation(self):
        FeatureFlag.objects.create(name='login_as_user', is_enabled=False)
        self.client.force_login(self.admin)
        self.client.get(reverse('login-as', args=[self.target.pk]))
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

    def test_enabled_allows_impersonation(self):
        """Control: the flag mechanism itself isn't what's broken."""
        FeatureFlag.objects.create(name='login_as_user', is_enabled=True)
        self.client.force_login(self.admin)
        self.client.get(reverse('login-as', args=[self.target.pk]))
        self.assertEqual(self.client.session['_auth_user_id'], self.target.pk)

    def test_admin_site_entry_point_also_blocked_when_disabled(self):
        """
        The second, independent impersonation entry point —
        `ParliamentUserAdmin.login_as_user`, registered via `admin_view()` —
        needs its own coverage since it isn't a call into `login_as_view`
        and couldn't inherit a decorator even if one were stacked there.
        """
        FeatureFlag.objects.create(name='login_as_user', is_enabled=False)
        self.client.force_login(self.admin)
        url = reverse('admin:login_as_user', args=[self.target.pk])
        self.client.get(url)
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

    def test_admin_site_entry_point_works_when_enabled(self):
        FeatureFlag.objects.create(name='login_as_user', is_enabled=True)
        self.client.force_login(self.admin)
        url = reverse('admin:login_as_user', args=[self.target.pk])
        self.client.get(url)
        self.assertEqual(self.client.session['_auth_user_id'], self.target.pk)

    def test_return_still_works_after_flag_disabled_mid_impersonation(self):
        """
        The specific property `login_as_view`'s docstring promises: turning
        the flag off must not trap someone who is already impersonating —
        only the START of impersonation is gated, never the return.
        """
        FeatureFlag.objects.create(name='login_as_user', is_enabled=True)
        self.client.force_login(self.admin)
        self.client.get(reverse('login-as', args=[self.target.pk]))
        self.assertEqual(self.client.session['_auth_user_id'], self.target.pk)

        FeatureFlag.objects.filter(name='login_as_user').update(is_enabled=False)

        response = self.client.get(reverse('return_to_original_user'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['_auth_user_id'], self.admin.pk)

    def test_login_as_link_hidden_when_disabled(self):
        FeatureFlag.objects.create(name='login_as_user', is_enabled=False)
        from src.admin import ParliamentUserAdmin
        from django.contrib import admin as django_admin
        model_admin = ParliamentUserAdmin(self.target.__class__, django_admin.site)
        self.assertEqual(model_admin.login_as_link(self.target), '—')

    def test_login_as_link_shown_when_enabled(self):
        FeatureFlag.objects.create(name='login_as_user', is_enabled=True)
        from src.admin import ParliamentUserAdmin
        from django.contrib import admin as django_admin
        model_admin = ParliamentUserAdmin(self.target.__class__, django_admin.site)
        self.assertIn('Login As User', model_admin.login_as_link(self.target))
