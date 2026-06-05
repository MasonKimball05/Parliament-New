"""
Passkey (WebAuthn) registration and authentication views.

Registration flow (authenticated user adding a passkey):
  POST /accounts/passkeys/register/begin/    → options JSON
  POST /accounts/passkeys/register/complete/ → saves credential, 200/400

Authentication flow (unauthenticated user signing in with a passkey):
  POST /accounts/passkeys/authenticate/begin/    → options JSON
  POST /accounts/passkeys/authenticate/complete/ → logs in + 2FA-verified, 200/400

Management:
  POST /accounts/passkeys/<int:pk>/delete/ → deletes credential (owner only)

On successful passkey authentication the user is fully logged in and the session
is flagged `webauthn_authenticated = True`. Enforce2FAMiddleware checks this flag
and skips the TOTP verify step, making passkeys a true bypass for password + 2FA.
"""
import json
import base64
import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import webauthn
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    UserVerificationRequirement,
    ResidentKeyRequirement,
    PublicKeyCredentialDescriptor,
)
from webauthn import base64url_to_bytes, options_to_json

from src.models.webauthn import WebAuthnCredential

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')

_SESSION_REG_CHALLENGE = 'webauthn_reg_challenge'
_SESSION_AUTH_CHALLENGE = 'webauthn_auth_challenge'


def _rp_config(request=None):
    """Return (rp_id, origin) derived from SITE_URL or the current request."""
    from urllib.parse import urlparse
    if settings.DEBUG and request is not None:
        # Use the actual host/port the browser connected to so the origin matches
        scheme = 'https' if request.is_secure() else 'http'
        origin = f'{scheme}://{request.get_host()}'
        rp_id = request.get_host().split(':')[0]  # strip port
    else:
        site_url = getattr(settings, 'SITE_URL', 'https://am-parliament.org')
        parsed = urlparse(site_url)
        rp_id = parsed.hostname
        origin = f'{parsed.scheme}://{parsed.netloc}'
    return rp_id, origin


# ── Registration ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def passkey_register_begin(request):
    """
    Generate and return WebAuthn registration options.
    The challenge is stored in the session for verification in the next step.
    """
    rp_id, _ = _rp_config(request)
    user = request.user

    # Exclude already-registered credentials so the authenticator doesn't re-register so the authenticator doesn't re-register
    existing = [
        PublicKeyCredentialDescriptor(id=bytes(cred.credential_id))
        for cred in user.webauthn_credentials.all()
    ]

    options = webauthn.generate_registration_options(
        rp_id=rp_id,
        rp_name='Parliament',
        user_id=str(user.pk).encode(),
        user_name=user.username,
        user_display_name=getattr(user, 'name', user.username),
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=existing,
    )

    # Store challenge as list of ints for JSON-safe session serialization
    request.session[_SESSION_REG_CHALLENGE] = list(options.challenge)

    return JsonResponse(json.loads(options_to_json(options)))


@login_required
@require_POST
def passkey_register_complete(request):
    """
    Verify the authenticator's registration response and save the credential.
    Expects the raw WebAuthn response JSON in the request body, plus an optional
    `X-Passkey-Name` header for the user-assigned display name.
    """
    rp_id, origin = _rp_config(request)
    challenge_list = request.session.pop(_SESSION_REG_CHALLENGE, None)
    if not challenge_list:
        return JsonResponse({'error': 'No registration in progress.'}, status=400)

    name = request.headers.get('X-Passkey-Name', '').strip()[:100] or 'Passkey'

    try:
        verification = webauthn.verify_registration_response(
            credential=request.body.decode(),
            expected_challenge=bytes(challenge_list),
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as exc:
        logger.warning(f'Passkey registration failed for {request.user.username}: {exc}')
        return JsonResponse({'error': 'Registration verification failed.'}, status=400)

    WebAuthnCredential.objects.create(
        user=request.user,
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        aaguid=str(verification.aaguid) if verification.aaguid else '',
        name=name,
    )

    logger.info(f'Passkey registered for {request.user.username}: "{name}"')
    return JsonResponse({'ok': True, 'name': name})


# ── Authentication ────────────────────────────────────────────────────────────

@require_POST
def passkey_authenticate_begin(request):
    """
    Generate authentication options. No credentials are specified so the
    authenticator can discover resident keys for this RP on its own.
    """
    rp_id, _ = _rp_config(request)

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    request.session[_SESSION_AUTH_CHALLENGE] = list(options.challenge)

    return JsonResponse(json.loads(options_to_json(options)))


@require_POST
def passkey_authenticate_complete(request):
    """
    Verify the authenticator's assertion, look up the credential owner, and log
    them in. Sets `webauthn_authenticated = True` in the session so
    Enforce2FAMiddleware skips the TOTP verify step.
    """
    rp_id, origin = _rp_config(request)
    challenge_list = request.session.pop(_SESSION_AUTH_CHALLENGE, None)
    if not challenge_list:
        return JsonResponse({'error': 'No authentication in progress.'}, status=400)

    try:
        body_json = json.loads(request.body)
    except Exception as exc:
        logger.warning(f'Passkey auth parse error: {exc}')
        return JsonResponse({'error': 'Invalid credential data.'}, status=400)

    # Look up credential by ID from the raw JSON
    try:
        cred_id_bytes = base64url_to_bytes(body_json['rawId'])
    except Exception as exc:
        logger.warning(f'Passkey auth: bad rawId: {exc}')
        return JsonResponse({'error': 'Invalid credential data.'}, status=400)

    db_cred = WebAuthnCredential.objects.select_related('user').filter(
        credential_id=cred_id_bytes
    ).first()

    if not db_cred:
        security_logger.warning(f'Passkey auth: unknown credential ID from {request.META.get("REMOTE_ADDR")}')
        return JsonResponse({'error': 'Unknown credential.'}, status=400)

    try:
        verification = webauthn.verify_authentication_response(
            credential=request.body.decode(),
            expected_challenge=bytes(challenge_list),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(db_cred.public_key),
            credential_current_sign_count=db_cred.sign_count,
        )
    except Exception as exc:
        security_logger.warning(
            f'Passkey auth verification failed for credential owner '
            f'{db_cred.user.username}: {exc}'
        )
        return JsonResponse({'error': 'Authentication verification failed.'}, status=400)

    # Update sign count and last-used timestamp
    db_cred.mark_used(verification.new_sign_count)

    user = db_cred.user
    if not user.is_active:
        return JsonResponse({'error': 'Account is disabled.'}, status=403)
    if getattr(user, 'is_quarantined', False):
        return JsonResponse({'error': 'Account is locked.'}, status=403)

    # Log the user in (bypasses password check — WebAuthn already verified identity)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    # Mark session as fully authenticated — Enforce2FAMiddleware checks this flag
    request.session['webauthn_authenticated'] = True

    # Also satisfy django-otp so is_verified() returns True if user has a TOTP device
    try:
        from django_otp import login as otp_login
        from django_otp.plugins.otp_totp.models import TOTPDevice
        totp_device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if totp_device:
            otp_login(request, totp_device)
    except Exception:
        pass  # otp_login failure is non-fatal — middleware will fall back to session flag

    logger.info(f'Passkey authentication successful for {user.username} (credential: "{db_cred.name}")')
    security_logger.info(
        f'[PASSKEY] Login: {user.username} via passkey "{db_cred.name}" '
        f'from {request.META.get("REMOTE_ADDR", "unknown")}'
    )

    return JsonResponse({'ok': True, 'redirect': '/'})


# ── Management ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def passkey_delete(request, pk):
    """Delete one of the current user's passkeys."""
    cred = get_object_or_404(WebAuthnCredential, pk=pk, user=request.user)
    name = cred.name
    cred.delete()
    logger.info(f'Passkey "{name}" deleted by {request.user.username}')
    return redirect('profile')
