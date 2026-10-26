"""
Every ``/api/mobile/`` view authenticates with ``MobileTokenAuthentication``.

WHY: ``MobileTokenAuthentication`` is the mobile 2FA gate — a device row only
exists after the full sign-in, and the class re-checks the device on every
request. ``Enforce2FAMiddleware`` deliberately has no ``/api/mobile/``
exemption; a Bearer request passes it only because it carries no session user.
So a mobile view that ALSO accepted ``SessionAuthentication`` (DRF's project
default) would let a cookie in, and one with no authentication would be open.
Either mistake is one missing line in a new view and invisible in review.

The rule, checked for every URL pattern in the ``mobile`` namespace:

* ``authentication_classes`` is exactly ``[MobileTokenAuthentication]`` and the
  view subclasses ``MobileAPIView`` (its ``{"error": code}`` errors and the
  ``mobile`` throttle scope), or
* the view is one of the pre-auth sign-in endpoints in ``PRE_AUTH`` below, and
  then ``authentication_classes == []`` and ``permission_classes == [AllowAny]``
  (they mint nothing usable without the full sign-in) and it subclasses
  ``PreAuthAPIView`` (per-IP / per-device throttles).

Adding a pre-auth endpoint means adding its URL name here, on purpose.
"""
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import AllowAny

from src.mobile.api import MobileAPIView, PreAuthAPIView
from src.mobile.authentication import MobileTokenAuthentication

PRE_AUTH = {
    'mobile_auth_login',
    'mobile_auth_verify',
    'mobile_auth_passkey_begin',
    'mobile_auth_passkey_complete',
    'mobile_auth_refresh',
}


def _mobile_patterns():
    """(url_name, callback) for every pattern under the 'mobile' namespace."""
    found = []

    def walk(patterns, in_mobile):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, in_mobile or p.namespace == 'mobile')
            elif isinstance(p, URLPattern) and in_mobile:
                found.append((p.name, p.callback))

    walk(get_resolver().url_patterns, False)
    return found


def _view_class(callback):
    return getattr(callback, 'cls', None) or getattr(callback, 'view_class', None)


class MobileViewsUseMobileAuthTests(SimpleTestCase):

    def test_the_namespace_is_found(self):
        names = {name for name, _ in _mobile_patterns()}
        self.assertTrue(PRE_AUTH <= names, f'missing pre-auth routes: {PRE_AUTH - names}')
        self.assertIn('mobile_me', names)

    def test_every_mobile_view_uses_bearer_auth_or_is_an_allowlisted_pre_auth_view(self):
        problems = []
        for name, callback in _mobile_patterns():
            cls = _view_class(callback)
            if cls is None or not hasattr(cls, 'authentication_classes'):
                problems.append(f'{name}: not a DRF view (no authentication_classes)')
                continue
            auth = list(cls.authentication_classes)
            perms = list(cls.permission_classes)
            if name in PRE_AUTH:
                if auth != []:
                    problems.append(f'{name}: pre-auth view must have authentication_classes == [] '
                                    f'(has {auth})')
                if perms != [AllowAny]:
                    problems.append(f'{name}: pre-auth view must have permission_classes == '
                                    f'[AllowAny] (has {perms})')
                if not issubclass(cls, PreAuthAPIView):
                    problems.append(f'{name}: pre-auth view must subclass PreAuthAPIView')
            else:
                if auth != [MobileTokenAuthentication]:
                    problems.append(f'{name}: authentication_classes must be exactly '
                                    f'[MobileTokenAuthentication] (has {auth})')
                if not issubclass(cls, MobileAPIView):
                    problems.append(f'{name}: must subclass MobileAPIView (error shape, throttle)')
        self.assertEqual(problems, [], '\n'.join(problems))

    def test_unnamed_routes_cannot_hide(self):
        unnamed = [cb for name, cb in _mobile_patterns() if not name]
        self.assertEqual(unnamed, [], 'every mobile route needs a name (the allowlist is by name)')
