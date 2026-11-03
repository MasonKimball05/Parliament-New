"""
Your passkeys in the app (v4.4.3): list, add, rename, remove, through the
website's own code (``src/view/webauthn.py``: ``passkey_registration_options``,
``verify_passkey_registration``, ``save_registered_passkey``,
``rename_passkey``, ``remove_passkey``, ``check_credential_change_password``).

* ``GET account/passkeys/`` → ``{passkeys: [{id, name, created_at,
  last_used_at}]}`` — the profile page's list. Never key material.
* ``POST account/passkeys/register/begin/`` ``{password}`` →
  ``{challenge_id, expires_at, options}``. The website asks for the password
  before a passkey is added (v3.13.2: an unattended session must not be able to
  plant a passkey, a backdoor past password and 2FA), so the app does too,
  from the same failure budget (``cred_change_attempts_<pk>``, 5 per 15
  minutes, shared with the website's register / remove and 2FA disable).
  The options are the website's (existing keys excluded, resident key
  preferred, user verification required). The challenge is a
  ``MobileAuthTicket`` bound to this member and this install, single use,
  three minutes.
* ``POST account/passkeys/register/complete/`` ``{challenge_id, credential,
  name}`` → ``{passkey}``. Verified with user verification enforced and the
  app's origins (the site origin plus the Android APK origins, as the app's
  other passkey flows); saved with the website's log line and the "A new
  passkey was added" notification and email.
* ``POST account/passkeys/<id>/rename/`` ``{name}`` → ``{passkey}`` (no
  password, as on the website).
* ``POST account/passkeys/<id>/delete/`` ``{password}`` → ``{ok, signed_out}``.
  The password, as on the website; the same log line and notification. Removing
  a passkey signs the app out everywhere (``src/mobile/revocation.py``); this
  install is kept unless it signed in with a passkey (it might be this one), in
  which case ``signed_out`` is true and its next request signs it out.

Another member's passkey is a 404. Errors: ``reauth_failed`` (400, wrong or
missing password), ``reauth_locked`` (429, the shared limit), ``invalid_challenge``
(400, unknown / used / expired / another install's), ``already_registered``
(400), ``passkey_rejected`` (400, verification failed), ``invalid_input`` (400).
None of these are codes the app signs out on.

The web passkey views have no page toggle; the pledge middleware always allows
``passkey_*``. The list mirrors the profile page, so it follows that page's
toggle as the app's profile screen does.
"""
import json
import logging

from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models.mobile import MobileAuthTicket
from src.models.webauthn import WebAuthnCredential

logger = logging.getLogger(__name__)

VIA = ' from the app'


class PasskeyError(exceptions.APIException):
    status_code = 400
    default_code = 'invalid_input'

    def __init__(self, code, message, status=400):
        super().__init__(message, code=code)
        self.status_code = status


def _iso(dt):
    return dt.isoformat() if dt else None


def passkey_json(cred):
    return {
        'id': cred.pk,
        'name': cred.name,
        'created_at': _iso(cred.created_at),
        'last_used_at': _iso(cred.last_used_at),
    }


def _data(request):
    return request.data if isinstance(request.data, dict) else {}


def _confirm_password(request, data, log_prefix):
    """The website's credential-change password check, same bucket and messages."""
    from src.view.webauthn import check_credential_change_password
    password = data.get('password')
    if not isinstance(password, str) or not password:
        raise PasskeyError('reauth_failed', 'Enter your password.')
    if len(password) > 4096:
        raise PasskeyError('reauth_failed', 'Incorrect password.')
    result = check_credential_change_password(request._request, request.user, password, log_prefix)
    if result == 'rate_limited':
        raise PasskeyError('reauth_locked', 'Too many failed attempts. Try again in 15 minutes.', 429)
    if result != 'ok':
        raise PasskeyError('reauth_failed', 'Incorrect password.')


def _own(request, pk):
    return get_object_or_404(WebAuthnCredential, pk=pk, user=request.user)


class PasskeyListView(MobileAPIView):
    web_page = 'profile'

    def get(self, request):
        creds = WebAuthnCredential.objects.filter(user=request.user).order_by('created_at')
        return Response({'passkeys': [passkey_json(c) for c in creds]})


class PasskeyRegisterBeginView(MobileAPIView):
    web_page = 'passkey_register_begin'
    web_page_toggle = False

    def post(self, request):
        from webauthn import options_to_json
        from src.mobile.views_auth import _b64url
        from src.view.webauthn import passkey_registration_options, rp_config

        _confirm_password(request, _data(request), 'passkey_register_attempts (app)')
        rp_id, _ = rp_config(request._request)
        options = passkey_registration_options(request.user, rp_id)
        raw, ticket = MobileAuthTicket.issue(
            MobileAuthTicket.PURPOSE_PASSKEY_REGISTER, user=request.user, device=request.mobile_device,
            payload={'challenge': _b64url(options.challenge)})
        return Response({
            'challenge_id': raw,
            'expires_at': ticket.expires_at.isoformat(),
            'options': json.loads(options_to_json(options)),
        })


class PasskeyRegisterCompleteView(MobileAPIView):
    web_page = 'passkey_register_complete'
    web_page_toggle = False

    def post(self, request):
        from webauthn import base64url_to_bytes
        from src.mobile.views_auth import expected_passkey_origins
        from src.view.webauthn import rp_config, save_registered_passkey, verify_passkey_registration

        user = request.user
        data = _data(request)
        challenge_id, credential = data.get('challenge_id'), data.get('credential')
        name = data.get('name', '')
        if not isinstance(challenge_id, str) or not challenge_id or len(challenge_id) > 200 \
                or not isinstance(credential, (dict, str)) or not isinstance(name, str):
            raise PasskeyError('invalid_input', 'challenge_id and credential are required.')
        name = name.strip()[:100] or 'Passkey'

        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_PASSKEY_REGISTER, challenge_id,
                                          device=request.mobile_device)
        if ticket is None or ticket.user_id != user.pk:
            logger.warning(f'App passkey registration: unknown, used or expired challenge from {user.username}')
            raise PasskeyError('invalid_challenge', 'That passkey request expired. Try again.')
        try:
            cred = json.loads(credential) if isinstance(credential, str) else credential
            if not isinstance(cred, dict):
                raise ValueError('credential is not an object')
            credential_json = json.dumps(cred)
        except Exception:
            raise PasskeyError('invalid_input', 'Invalid credential data.')

        rp_id, _ = rp_config(request._request)
        try:
            verification = verify_passkey_registration(
                credential_json, base64url_to_bytes(ticket.payload['challenge']),
                rp_id, expected_passkey_origins(request), require_user_verification=True)
        except Exception as exc:
            logger.warning(f'Passkey registration from the app failed for {user.username}: {exc}')
            raise PasskeyError('passkey_rejected', 'Registration verification failed.')

        # The exclude list stops a phone re-registering a key it already holds;
        # this catches anything that slips past it (the web would hit the
        # unique constraint).
        if WebAuthnCredential.objects.filter(credential_id=verification.credential_id).exists():
            raise PasskeyError('already_registered', 'This passkey is already registered.')
        saved = save_registered_passkey(request._request, user, verification, name, via=VIA)
        return Response({'passkey': passkey_json(saved)})


class PasskeyRenameView(MobileAPIView):
    web_page = 'passkey_rename'
    web_page_toggle = False

    def post(self, request, pk):
        from src.view.webauthn import rename_passkey
        cred = _own(request, pk)
        name = rename_passkey(request.user, cred, _data(request).get('name'))
        if name is None:
            raise PasskeyError('invalid_input', 'Name cannot be empty.')
        return Response({'passkey': passkey_json(cred)})


class PasskeyDeleteView(MobileAPIView):
    web_page = 'passkey_delete'
    web_page_toggle = False

    def post(self, request, pk):
        from src.view.webauthn import remove_passkey
        cred = _own(request, pk)
        _confirm_password(request, _data(request), 'cred_remove_attempts (app)')
        device = request.mobile_device
        # A passkey sign-in may have been this passkey: that install signs in again.
        signed_out = device.verified_via == 'passkey'
        remove_passkey(request._request, request.user, cred, via=VIA,
                       keep_mobile_device_id=None if signed_out else device.pk)
        return Response({'ok': True, 'signed_out': signed_out})
