"""
``POST /m/handoff/``: turn a web-session code into a site session for the
app's web view (mobile plan, Phase 1).

The app gets the code from ``POST /api/mobile/v1/auth/web-session/`` and opens
the web view with react-native-webview's
``source={{uri: '/m/handoff/', method: 'POST',
body: 'code=…&access_token=…&next=…'}}``.

The access token may come as the form field ``access_token`` (POST body only)
or as ``Authorization: Bearer <access>``. The body field exists because
react-native-webview's Android POST source (``WebView.postUrl``) drops every
custom header, so the header can never arrive there. If both are sent they
must be the same token. ``access_token`` (or ``code``) in the query string is
refused outright: query strings end up in access logs and proxies.

What a successful handoff does, in the same order as the password login:

1. checks the access token exactly like ``MobileTokenAuthentication`` (live
   access token, ``still_allowed``; same function for header and body), then
   consumes the code **for that same device** (single use, 60 s);
2. flushes whatever session the web view already had, then ``login()`` with
   ``AUTH_BACKEND_PATH`` — even for the same user, so no earlier 2FA,
   passkey, impersonation or dismissal state is inherited (``login()`` alone
   only cycles the key when the user is unchanged);
3. marks the second factor the way the website does — ``django_otp.login``
   with the member's device, and ``webauthn_authenticated`` for a passkey
   device — so ``Enforce2FAMiddleware`` / ``session_passes_second_factor``
   treat the session like one that signed in on the site. A device that signed
   in with a password alone never gets here once the member has a second
   factor or one is required: ``still_allowed`` fails for it
   (``second_factor_required``) and step 1 refuses;
4. writes the same ``login_geo`` / ``login_geo_suspicious`` /
   ``login_geo_country`` / ``login_geo_city`` keys ``run_post_auth_pipeline``
   writes on a website sign-in, from the geo recorded on the device at app
   sign-in, escalated (never cleared) by a CACHED lookup of this request's IP,
   so ``GeoRestrictionMiddleware`` blocks the non-US exports here too;
5. stores ``mobile_device_id``, which ``MobileSessionGuardMiddleware`` (HTTP)
   and ``SecondFactorWebsocketMiddleware`` (websockets) check, so revoking the
   device ends this session too;
6. records the ``UserSession`` row (Active Sessions page);
7. 303s to a safe ``next`` with ``Referrer-Policy: no-referrer``.

Login signals: ``user_logged_in`` still fires (Django's ``update_last_login``
must run, and it is how ``login()`` works), but ``request._mobile_handoff`` is
set first and the two receivers that record a *sign-in* — ``LoginHistory`` /
risk alerts in ``src/signals.py`` and the ActivityLog ``login`` row in
``src/middleware/activity_logging.py`` — return early for it. The device was
recorded, risk-scored and alerted on when the app signed in; opening the web
view is not a new sign-in, and doing it again on every web-view open would
flood LoginHistory and raise "new device" alerts for the member's own phone.
``run_post_auth_pipeline`` is not run for the same reason (its geo verdict is
copied from the device instead, step 4).

Every failure is a bare 403. It never redirects to the login page: a web view
that silently showed a password form would teach members to type their
password into whatever the app shows them.
"""
import logging

from django.contrib.auth import login
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseRedirect
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework.exceptions import AuthenticationFailed

from src.auth_backends import AUTH_BACKEND_PATH
from src.mobile.authentication import MobileTokenAuthentication
from src.models.mobile import MobileAuthTicket
from src.next_url import safe_next

logger = logging.getLogger('admin_actions')

#: Request attribute the login receivers check (see the module docstring).
HANDOFF_FLAG = '_mobile_handoff'


def _forbidden():
    response = HttpResponseForbidden('This sign-in link is no longer valid. Close this page and try again.',
                                     content_type='text/plain; charset=utf-8')
    response['Referrer-Policy'] = 'no-referrer'
    return response


def _mark_second_factor(request, user, device):
    """Same session state the website's own sign-in leaves behind."""
    from django_otp import login as otp_login, user_has_device
    from django_otp.plugins.otp_static.models import StaticDevice
    from django_otp.plugins.otp_totp.models import TOTPDevice

    if device.verified_via == 'passkey':
        # src/view/webauthn.py: the flag, plus otp_login so is_verified() is True.
        request.session['webauthn_authenticated'] = True
        totp = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if totp is not None:
            otp_login(request, totp)
        return
    if device.verified_via in ('password_totp', 'password_backup'):
        otp_device = (TOTPDevice.objects.filter(user=user, confirmed=True).first()
                      or StaticDevice.objects.filter(user=user, confirmed=True).first())
        if otp_device is not None:
            otp_login(request, otp_device)
        return
    # 'password': the device signed in with no second factor, and
    # still_allowed() guarantees the member still has none and needs none.
    # Belt and braces: never mark anything; the web view would get the verify step.
    if user_has_device(user):
        logger.info('mobile handoff: device %s predates 2FA for %s; verify step required',
                    device.pk, user.pk)


def _write_geo(request, device):
    """The ``login_geo_*`` keys ``run_post_auth_pipeline`` writes, for the web view."""
    from src.geo_utils import cached_is_foreign_ip
    from src.utils.security_utils import get_client_ip

    geo = dict(device.login_geo or {})
    suspicious = bool(device.login_geo_suspicious)
    try:
        here_foreign, here_geo = cached_is_foreign_ip(get_client_ip(request))
    except Exception:
        here_foreign, here_geo = False, {}
    if here_foreign and not suspicious:
        suspicious, geo = True, here_geo
    request.session['login_geo'] = geo
    request.session['login_geo_suspicious'] = suspicious
    if geo:
        request.session['login_geo_country'] = geo.get('country', '')
        request.session['login_geo_city'] = geo.get('city', '')


def _authenticate(request):
    """
    ``(user, device)`` from the ``access_token`` form field and/or the Bearer
    header, or None. Both go through ``authenticate_access_token``, so expiry,
    ``still_allowed`` and the device binding are identical either way.
    """
    auth = MobileTokenAuthentication()
    parts = request.META.get('HTTP_AUTHORIZATION', '').split()
    header_token = parts[1] if len(parts) == 2 and parts[0].lower() == 'bearer' else None
    if parts and header_token is None:
        return None   # a malformed Authorization header is never ignored
    body_token = request.POST.get('access_token') or None
    if header_token and body_token and header_token != body_token:
        return None
    raw = body_token or header_token
    if not raw:
        return None
    try:
        return auth.authenticate_access_token(request, raw)
    except AuthenticationFailed:
        return None


def _cross_site(request):
    """
    True when a browser says another site started this request. Stops "login
    CSRF": an attacker auto-posting THEIR OWN code + token from their page to
    sign a victim's browser into the attacker's account. The app's own loads
    are browser-initiated (``Sec-Fetch-Site: none``, no foreign ``Origin``).
    """
    if request.META.get('HTTP_SEC_FETCH_SITE', '') in ('cross-site', 'same-site'):
        return True
    origin = request.META.get('HTTP_ORIGIN', '')
    if origin and origin != 'null':
        from django.conf import settings
        ours = {request.build_absolute_uri('/').rstrip('/'),
                *(o.rstrip('/') for o in getattr(settings, 'CSRF_TRUSTED_ORIGINS', []))}
        return origin.rstrip('/') not in ours
    return False


# csrf_exempt, deliberately and only here. A CSRF token protects a request
# whose credential the browser attaches by itself (a cookie). This view does
# not trust any cookie: it requires BOTH the one-time code AND the access token
# of the SAME device the code was issued to, and neither is ambient: another
# origin can't read either (the code is fetched with the token over the
# Bearer-only API, which sends no CORS headers, so no other origin can read
# its responses). A cross-site form could post fields, but it has no values to
# put in them. There is also no CSRF cookie yet in a fresh web view to check
# against: requiring one would make the very first request fail. A request a
# browser marks cross-site is refused (_cross_site) so nobody can post their
# OWN code + token into someone else's browser.
#
# Why the token may ride in the POST body (the Android web view can't send
# the header): a form body is not written to access logs (they log the
# request line, and `access_token` / `code` in the query string are refused
# below), is never part of a URL so never leaks through Referer or history,
# is redacted from error reports (sensitive_post_parameters) and skipped by
# InputSanitizationMiddleware's attack log. The token alone opens nothing
# here without a fresh single-use code for the same device, and it is the
# 15-minute access token, never the refresh token. The page never sees it:
# the web view sends the body natively; no script holds the token.
@csrf_exempt
@never_cache
@sensitive_post_parameters('access_token', 'code')
def mobile_web_handoff(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if 'access_token' in request.GET or 'code' in request.GET:
        logger.warning('mobile handoff refused: credential in the query string')
        return _forbidden()
    if _cross_site(request):
        logger.warning('mobile handoff refused: cross-site request')
        return _forbidden()

    result = _authenticate(request)
    if result is None:
        return _forbidden()
    user, device = result

    ticket = MobileAuthTicket.consume(
        MobileAuthTicket.PURPOSE_WEB_HANDOFF, request.POST.get('code', ''), device=device)
    if ticket is None or ticket.user_id != user.pk:
        logger.warning('mobile handoff refused: bad/used code for device %s (user %s)',
                       device.pk, user.pk)
        return _forbidden()

    setattr(request, HANDOFF_FLAG, True)
    request.session.flush()
    login(request, user, backend=AUTH_BACKEND_PATH)
    _mark_second_factor(request, user, device)
    _write_geo(request, device)
    request.session['mobile_device_id'] = device.pk

    from src.models import UserSession
    UserSession.create_or_update_session(user, request)

    target = safe_next(request, request.POST.get('next', '')) or '/'
    response = HttpResponseRedirect(target, status=303)
    response['Referrer-Policy'] = 'no-referrer'
    return response
