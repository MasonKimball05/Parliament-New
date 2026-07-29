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
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
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
from src.utils.security_utils import get_client_ip, run_post_auth_pipeline
from src.auth_backends import AUTH_BACKEND_PATH

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')

_SESSION_REG_CHALLENGE = 'webauthn_reg_challenge'
_SESSION_AUTH_CHALLENGE = 'webauthn_auth_challenge'

# Password re-auth rate limiting (v3.13.2, specced in v3.5.1):
# 5 tries per 15 minutes per user, cache-keyed.
_REAUTH_MAX_FAILURES = 5
_REAUTH_WINDOW_SECONDS = 900


def _check_password_reauth(request, cache_prefix):
    """
    Verify the current password from request.POST for sensitive credential
    operations (passkey delete / register, 2FA disable). Returns one of:
      'ok'           — password correct, counter cleared
      'denied'       — missing/wrong password, counter incremented
      'rate_limited' — too many failures; refused even with a correct password

    All credential-change flows share ONE rate-limit bucket (v3.14.0 review
    fix): per-endpoint buckets let a hijacked session multiply its password
    guesses across endpoints. cache_prefix is kept for log attribution only.
    Vote confirmation ('vote_pw') deliberately stays a separate, laxer bucket.
    """
    from django.core.cache import cache
    key = f'cred_change_attempts_{request.user.pk}'
    failures = cache.get(key, 0)
    if failures >= _REAUTH_MAX_FAILURES:
        security_logger.warning(
            f'{cache_prefix}: rate-limited for {request.user.username} '
            f'from {get_client_ip(request)} ({failures} failed password attempts)'
        )
        return 'rate_limited'
    password = request.POST.get('password', '')
    if not password or not request.user.check_password(password):
        cache.set(key, failures + 1, _REAUTH_WINDOW_SECONDS)
        security_logger.warning(
            f'{cache_prefix}: bad password for {request.user.username} '
            f'from {get_client_ip(request)}'
        )
        return 'denied'
    cache.delete(key)
    return 'ok'


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

    Requires password re-authentication (v3.13.2, specced in v3.5.1): an
    attacker on an unattended session must not be able to register their own
    passkey — a persistent backdoor that bypasses password + TOTP and survives
    a password reset. Gating begin is sufficient: complete is useless without
    the session challenge begin creates.
    """
    reauth = _check_password_reauth(request, 'passkey_register_attempts')
    if reauth == 'rate_limited':
        return JsonResponse(
            {'error': 'Too many failed attempts. Try again in 15 minutes.'}, status=429)
    if reauth == 'denied':
        return JsonResponse(
            {'error': 'Incorrect password.'}, status=403)

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
            user_verification=UserVerificationRequirement.REQUIRED,
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

    ip_address = get_client_ip(request)
    security_logger.info(
        f'Passkey registered for {request.user.username}: "{name}" from {ip_address}')
    from src.security_notifications import notify_user_security_event
    notify_user_security_event(
        request.user,
        'A new passkey was added to your account',
        f'A new passkey ("{name}") was just registered on your Parliament account.',
        ip_address=ip_address,
    )
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
        user_verification=UserVerificationRequirement.REQUIRED,
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
            # Options request UV=REQUIRED; enforce it server-side too —
            # py_webauthn does NOT check the UV flag by default.
            require_user_verification=True,
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

    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')

    # Run shared post-auth pipeline: blacklist check, geo, LoginHistory, LoginAlert, watch-flag
    error_response, _ = run_post_auth_pipeline(request, user, ip_address, user_agent, method='passkey')
    if error_response:
        return error_response

    # Log the user in (bypasses password check — WebAuthn already verified identity)
    login(request, user, backend=AUTH_BACKEND_PATH)

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

    return JsonResponse({'ok': True, 'redirect': '/'})


# ── Re-authentication (sensitive-action confirmation) ─────────────────────────
#
# v3.13.3: lets an already-logged-in user confirm a sensitive action (casting a
# vote) with a passkey instead of retyping their password. Flow mirrors
# admin_v2's second-factor passkey auth: begin scopes allow_credentials to the
# current user's keys, complete verifies the assertion and stamps a short-lived
# ONE-SHOT grant in the session, which check_vote_reauth() consumes.

_SESSION_REAUTH_CHALLENGE = 'webauthn_reauth_challenge'
_SESSION_REAUTH_GRANT_AT = 'webauthn_reauth_grant_at'
_REAUTH_GRANT_MAX_AGE_SECONDS = 120

# Vote password confirmation gets a laxer ceiling than credential changes:
# mistyping a few times during a meeting shouldn't lock a member out of voting.
_VOTE_PW_MAX_FAILURES = 10


@login_required
@require_POST
def passkey_reauth_begin(request):
    """
    Generate assertion options for re-authenticating the CURRENT user.
    allow_credentials is scoped to their registered keys so the authenticator
    prompts immediately without needing a discoverable/resident key.
    """
    from django.core.cache import cache
    key = f'passkey_reauth_attempts_{request.user.pk}'
    if cache.get(key, 0) >= _REAUTH_MAX_FAILURES:
        return JsonResponse(
            {'error': 'Too many failed attempts. Try again in 15 minutes.'}, status=429)

    rp_id, _ = _rp_config(request)
    user_creds = list(request.user.webauthn_credentials.all())
    if not user_creds:
        return JsonResponse({'error': 'No passkeys registered for this account.'}, status=400)

    options = webauthn.generate_authentication_options(
        rp_id=rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=bytes(c.credential_id)) for c in user_creds
        ],
    )
    request.session[_SESSION_REAUTH_CHALLENGE] = list(options.challenge)
    return JsonResponse(json.loads(options_to_json(options)))


@login_required
@require_POST
def passkey_reauth_complete(request):
    """
    Verify the assertion and stamp a one-shot re-auth grant in the session.
    The credential must belong to the logged-in user — no lateral movement.
    """
    from django.core.cache import cache
    rp_id, origin = _rp_config(request)
    challenge_list = request.session.pop(_SESSION_REAUTH_CHALLENGE, None)
    if not challenge_list:
        return JsonResponse({'error': 'No re-authentication in progress.'}, status=400)

    key = f'passkey_reauth_attempts_{request.user.pk}'
    failures = cache.get(key, 0)
    if failures >= _REAUTH_MAX_FAILURES:
        return JsonResponse(
            {'error': 'Too many failed attempts. Try again in 15 minutes.'}, status=429)

    try:
        body_json = json.loads(request.body)
        cred_id_bytes = base64url_to_bytes(body_json['rawId'])
    except Exception:
        return JsonResponse({'error': 'Invalid credential data.'}, status=400)

    db_cred = WebAuthnCredential.objects.filter(
        credential_id=cred_id_bytes, user=request.user,
    ).first()
    if not db_cred:
        cache.set(key, failures + 1, _REAUTH_WINDOW_SECONDS)
        security_logger.warning(
            f'Passkey re-auth: unknown or unauthorized credential for '
            f'{request.user.username} from {get_client_ip(request)}'
        )
        return JsonResponse({'error': 'Unknown or unauthorized credential.'}, status=400)

    try:
        verification = webauthn.verify_authentication_response(
            credential=request.body.decode(),
            expected_challenge=bytes(challenge_list),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(db_cred.public_key),
            credential_current_sign_count=db_cred.sign_count,
            # Options request UV=REQUIRED; enforce it server-side too —
            # py_webauthn does NOT check the UV flag by default.
            require_user_verification=True,
        )
    except Exception as exc:
        cache.set(key, failures + 1, _REAUTH_WINDOW_SECONDS)
        security_logger.warning(
            f'Passkey re-auth verification failed for {request.user.username}: {exc}')
        return JsonResponse({'error': 'Passkey verification failed.'}, status=400)

    db_cred.mark_used(verification.new_sign_count)
    cache.delete(key)

    request.session[_SESSION_REAUTH_GRANT_AT] = timezone.now().isoformat()
    logger.info(
        f'Passkey re-auth OK for {request.user.username} (credential "{db_cred.name}")')
    return JsonResponse({'ok': True})


def consume_passkey_reauth_grant(request):
    """Pop the one-shot passkey re-auth grant. True if present and fresh."""
    stamp = request.session.pop(_SESSION_REAUTH_GRANT_AT, None)
    if not stamp:
        return False
    try:
        granted_at = timezone.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    return (timezone.now() - granted_at).total_seconds() <= _REAUTH_GRANT_MAX_AGE_SECONDS


def check_vote_reauth(request):
    """
    Identity confirmation for a vote POST: a one-shot passkey grant
    (auth_method=passkey, see passkey_reauth_begin/complete) or the account
    password. Returns (ok, error_message).

    Password failures are rate-limited per user (v3.13.3 — the vote forms
    previously allowed unlimited password guessing).
    """
    from django.core.cache import cache
    user = request.user

    if request.POST.get('auth_method') == 'passkey':
        if consume_passkey_reauth_grant(request):
            return True, None
        return False, 'Passkey confirmation expired or missing — please try again.'

    key = f'vote_pw_attempts_{user.pk}'
    failures = cache.get(key, 0)
    if failures >= _VOTE_PW_MAX_FAILURES:
        security_logger.warning(
            f'Vote password confirmation rate-limited for {user.username} '
            f'from {get_client_ip(request)}'
        )
        return False, ('Too many incorrect password attempts. Try again in '
                       '15 minutes, or confirm with a passkey.')
    password = request.POST.get('password', '')
    if not password or not user.check_password(password):
        cache.set(key, failures + 1, _REAUTH_WINDOW_SECONDS)
        return False, 'Incorrect password.'
    cache.delete(key)
    return True, None


# ── Management ────────────────────────────────────────────────────────────────

@login_required
@require_POST
def passkey_delete(request, pk):
    """Delete one of the current user's passkeys.

    Requires password re-authentication (v3.13.2, specced in v3.5.1): a
    walk-up attacker on an unattended session must not be able to remove
    the user's passkey.
    """
    cred = get_object_or_404(WebAuthnCredential, pk=pk, user=request.user)

    reauth = _check_password_reauth(request, 'cred_remove_attempts')
    if reauth == 'rate_limited':
        messages.error(request, 'Too many failed attempts. Try again in 15 minutes.')
        return redirect('profile')
    if reauth == 'denied':
        messages.error(request, 'Incorrect password. The passkey was NOT removed.')
        return redirect('profile')

    name = cred.name
    cred.delete()
    ip_address = get_client_ip(request)
    security_logger.info(
        f'Passkey "{name}" deleted by {request.user.username} from {ip_address} '
        f'(password re-auth passed)'
    )
    from src.security_notifications import notify_user_security_event
    notify_user_security_event(
        request.user,
        'A passkey was removed from your account',
        f'The passkey "{name}" was just removed from your Parliament account '
        f'after a correct password was entered.',
        ip_address=ip_address,
    )
    messages.success(request, f'Passkey "{name}" has been removed.')
    return redirect('profile')


@login_required
@require_POST
def passkey_rename(request, pk):
    """Rename one of the current user's passkeys. Expects JSON body {name: '...'}."""
    cred = get_object_or_404(WebAuthnCredential, pk=pk, user=request.user)
    try:
        body = json.loads(request.body)
        new_name = body.get('name', '').strip()[:100]
    except Exception:
        return JsonResponse({'error': 'Invalid request.'}, status=400)
    if not new_name:
        return JsonResponse({'error': 'Name cannot be empty.'}, status=400)
    old_name = cred.name
    cred.name = new_name
    cred.save(update_fields=['name'])
    logger.info(f'Passkey renamed by {request.user.username}: "{old_name}" → "{new_name}"')
    return JsonResponse({'ok': True, 'name': new_name})
