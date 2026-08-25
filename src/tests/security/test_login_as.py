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
