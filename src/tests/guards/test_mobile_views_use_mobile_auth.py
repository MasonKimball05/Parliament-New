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

One more kind (v3.60.0): the location check-in, in ``CHECKIN_KEY``, which a
background task calls while the phone is locked and so authenticates with the
device's narrow check-in key: exactly ``[CheckinKeyAuthentication]``,
``IsAuthenticated``, subclassing ``CheckinKeyAPIView``.
"""
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import AllowAny, IsAuthenticated

from src.mobile.api import CheckinKeyAPIView, MobileAPIView, PreAuthAPIView
from src.mobile.authentication import CheckinKeyAuthentication, MobileTokenAuthentication

PRE_AUTH = {
    'mobile_auth_login',
    'mobile_auth_verify',
    'mobile_auth_passkey_begin',
    'mobile_auth_passkey_complete',
    'mobile_auth_refresh',
}

CHECKIN_KEY = {'mobile_location_checkin'}


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
            elif name in CHECKIN_KEY:
                if auth != [CheckinKeyAuthentication] or perms != [IsAuthenticated]:
                    problems.append(f'{name}: must use exactly [CheckinKeyAuthentication] and '
                                    f'[IsAuthenticated] (has {auth}, {perms})')
                if not issubclass(cls, CheckinKeyAPIView):
                    problems.append(f'{name}: must subclass CheckinKeyAPIView')
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


def _all_url_names():
    names = set()

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns)
            elif isinstance(p, URLPattern) and p.name:
                names.add(p.name)

    walk(get_resolver().url_patterns)
    return names


class MobileViewsNameTheirWebPageTests(SimpleTestCase):
    """
    Phase 3: an app screen must honour its web page's toggle and pledge
    restriction. Both key on the WEB url_name (``home``), not the mobile one
    (``mobile_home``), so a mobile view that forgot ``web_page`` would quietly
    serve a page the site has switched off or restricted. Every mobile route
    except the app's own plumbing (the routes the pledge middleware always
    allows: sign-in, ``me/``, devices, push token, socket tickets) must name
    one, and it must be a real route.
    """

    def test_every_screen_endpoint_names_a_real_web_page(self):
        from src.middleware.pledge_pages import is_always_allowed
        names = _all_url_names()
        problems = []
        for name, callback in _mobile_patterns():
            if is_always_allowed(name):
                continue
            cls = _view_class(callback)
            page = getattr(cls, 'web_page', None)
            if not page:
                problems.append(f'{name}: set web_page to the web url_name it mirrors')
            elif page not in names:
                problems.append(f'{name}: web_page {page!r} is not a url name')
            elif page.startswith('mobile_'):
                problems.append(f'{name}: web_page must be the WEB page, not {page!r}')
        self.assertEqual(problems, [], '\n'.join(problems))
