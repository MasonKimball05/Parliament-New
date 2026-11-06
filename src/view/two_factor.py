"""
Two-Factor Authentication setup and verification views
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django.core.cache import cache
from django.core import signing
from django_otp import user_has_device, login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.util import random_hex
from src.utils.security_utils import get_client_ip
from src.models import WebAuthnCredential
from datetime import timedelta
import secrets
import qrcode
import qrcode.image.svg
import io
import logging

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')

# 2FA brute-force settings
_2FA_MAX_FAILURES = 5       # failures before alert
_2FA_WINDOW_SECONDS = 900   # 15-minute window

# Number of backup codes to generate
BACKUP_CODE_COUNT = 10

# Remember this device
_REMEMBER_COOKIE_NAME = '2fa_remember'
_REMEMBER_COOKIE_SALT = 'parliament.2fa.remember'
_REMEMBER_DAYS = 30


def _make_remember_cookie_value(user, device):
    """Return a signed value encoding user PK + device PK."""
    signer = signing.TimestampSigner(salt=_REMEMBER_COOKIE_SALT)
    return signer.sign(f'{user.pk}:{device.pk}')


def _parse_remember_cookie(cookie_value):
    """
    Validate and decode a remember cookie.
    Returns (user_pk, device_pk) on success, raises signing.BadSignature on failure.

    ⚠️ v3.24.0 — `user_pk` COMES BACK AS A STRING. It used to be `int(user_pk)`,
    and `ParliamentUser.user_id` is a CharField primary key: a brother's roll
    number looks numeric, but a generated id is `P-C7JKZY`, and `int()` on that
    raises `ValueError`.

    v3.23.0 found exactly this and fixed the *inline copy* of the parse in
    `Enforce2FAMiddleware._check_remember_cookie` — and this function, the named
    helper the same cookie has, was left as it was. Tenth instance of the shape
    CLAUDE.md records: **a rule stated correctly, and one reader left outside
    it.** Its one caller (`profile_view`) catches `ValueError` and reports the
    device as not remembered, so the two halves of the app now disagree about
    the same cookie: the middleware honours it and the profile page says it does
    not exist.

    `device_pk` stays an `int` — `TOTPDevice.pk` is a real AutoField, and the
    reason this bug existed is that somebody assumed a pk is a number. Being
    explicit about which one is which is the point.
    """
    signer = signing.TimestampSigner(salt=_REMEMBER_COOKIE_SALT)
    value = signer.unsign(cookie_value, max_age=_REMEMBER_DAYS * 86400)
    user_pk, device_pk = value.split(':', 1)
    return str(user_pk), int(device_pk)


def set_remember_cookie(response, user, device):
    """Attach a 30-day HttpOnly remember cookie to the response."""
    response.set_cookie(
        _REMEMBER_COOKIE_NAME,
        _make_remember_cookie_value(user, device),
        max_age=_REMEMBER_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite='Lax',
    )


def clear_remember_cookie(response):
    """Remove the remember cookie."""
    response.delete_cookie(_REMEMBER_COOKIE_NAME)


def _delete_devices(queryset, keep_mobile_device_id=None):
    """
    Delete OTP devices one by one so each ``post_delete`` sees the instance we
    tagged: src/mobile/revocation.py signs every app install out when a
    confirmed factor is deleted, sparing ``keep_mobile_device_id`` (the app
    install that asked, as a password change keeps it). The website passes
    nothing, which is what the queryset ``.delete()`` this replaced did.
    """
    for device in list(queryset):
        device._keep_mobile_device_id = keep_mobile_device_id
        device.delete()


def _generate_backup_codes(user, keep_mobile_device_id=None):
    """
    Generate a fresh set of backup codes for a user.
    Deletes any existing StaticDevice for this user and creates a new one.
    Returns a list of plaintext code strings (shown to user once).
    Clears backup_codes_acknowledged so the user is prompted to view the new codes.
    """
    # Remove any existing backup code device
    _delete_devices(StaticDevice.objects.filter(user=user, name='backup'), keep_mobile_device_id)

    device = StaticDevice.objects.create(user=user, name='backup', confirmed=True)
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        # 8-character alphanumeric, uppercase for readability
        code = secrets.token_hex(4).upper()  # e.g. "A3F2C19D"
        StaticToken.objects.create(device=device, token=code)
        codes.append(code)

    # Reset acknowledged flag — user must view new codes
    user.backup_codes_acknowledged = False
    user.save(update_fields=['backup_codes_acknowledged'])

    # Bust the per-request cache so the banner shows immediately
    from django.core.cache import cache
    cache.delete(f'2fa_status_{user.pk}')

    return codes


def _get_backup_device(user):
    """Return the user's confirmed backup StaticDevice, or None."""
    return StaticDevice.objects.filter(user=user, name='backup', confirmed=True).first()


# ── Shared with the app (src/mobile/views_two_factor.py) ─────────────────────

def manual_entry_key(device):
    """The TOTP secret as authenticator apps expect it: base32, in groups of four."""
    import base64
    raw = base64.b32encode(device.bin_key).decode('ascii').rstrip('=')
    return ' '.join(raw[i:i + 4] for i in range(0, len(raw), 4))


def pending_totp_device(user, fresh=False):
    """
    The unconfirmed TOTP device a setup shows, created if there is none.
    ``fresh=True`` (the app) replaces any pending one with a new key, so a
    secret is only ever handed out once; the website keeps showing the same
    pending key on reload, as it always has.
    """
    pending = TOTPDevice.objects.filter(user=user, confirmed=False)
    if fresh:
        pending.delete()   # never confirmed, so never a security event
    else:
        device = pending.first()
        if device:
            return device
    return TOTPDevice.objects.create(user=user, name='default', confirmed=False, key=random_hex())


def confirm_totp_setup(user, device, token, keep_mobile_device_id=None):
    """
    Confirm a pending TOTP ``device`` with ``token``. Returns the new backup
    codes (plaintext, to be shown once) or None for a wrong code.
    """
    if not device.verify_token(token):
        return None
    device.confirmed = True
    device.save(update_fields=['confirmed'])
    return _generate_backup_codes(user, keep_mobile_device_id=keep_mobile_device_id)


def regenerate_backup_codes(user, via='', keep_mobile_device_id=None):
    """Replace ``user``'s backup codes; returns the new ones (shown once)."""
    codes = _generate_backup_codes(user, keep_mobile_device_id=keep_mobile_device_id)
    logger.info(f"User {user.username} regenerated 2FA backup codes.{via}")
    return codes


def disable_two_factor(user, ip_address, via='', keep_mobile_device_id=None):
    """
    Remove ``user``'s authenticator and backup codes (the password has
    already been confirmed), with the website's log line and the "Two-Factor
    Authentication was disabled" notification and email.
    """
    _delete_devices(TOTPDevice.objects.filter(user=user), keep_mobile_device_id)
    _delete_devices(StaticDevice.objects.filter(user=user, name='backup'), keep_mobile_device_id)
    security_logger.info(
        f'2FA disabled by {user.username} from {ip_address} (password re-auth passed){via}'
    )
    from src.security_notifications import notify_user_security_event
    notify_user_security_event(
        user,
        'Two-Factor Authentication was disabled on your account',
        'Two-Factor Authentication (TOTP and backup codes) was just disabled on your '
        'Parliament account after a correct password was entered.',
        ip_address=ip_address,
    )


@login_required
def two_factor_setup(request):
    """
    Set up Two-Factor Authentication using TOTP (Time-based One-Time Password)
    Users scan QR code with Google Authenticator, Authy, or similar app.

    If the user has no email address, show an email collection step first —
    this keeps everything on the already-middleware-exempt /accounts/two-factor/setup/
    path so a policy-required user can still complete setup.
    """
    # Check if user already has 2FA set up
    if user_has_device(request.user):
        messages.info(request, 'Two-Factor Authentication is already set up for your account.')
        return redirect('home')

    user = request.user

    # --- Email collection step ---
    # If the user has no email we stop here and collect it first.
    # Handle email submission in this same view (POST with 'email' field)
    # so we stay on the middleware-exempt path.
    if not user.email:
        if request.method == 'POST' and 'email' in request.POST:
            email = request.POST.get('email', '').strip().lower()
            if email:
                from django.db import transaction as _tx
                with _tx.atomic():
                    user.email = email
                    user.email_flagged = False
                    user.email_flagged_reason = ''
                    user.email_flagged_at = None
                    user.save(update_fields=['email', 'email_flagged', 'email_flagged_reason', 'email_flagged_at'])
                user.refresh_from_db()
                logger.info(f'Email set for {user.username} during 2FA setup: {email}')
                messages.success(request, f'Email set to {email}. You can now complete 2FA setup.')
                return redirect('two_factor_setup')
            else:
                messages.error(request, 'Please enter a valid email address.')
        # Render the email collection step (no QR code yet)
        return render(request, 'two_factor/setup.html', {'needs_email': True})

    # --- Normal TOTP setup ---
    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()

    if request.method == 'POST' and 'token' in request.POST:
        token = request.POST.get('token', '').strip()

        if not device:
            messages.error(request, 'No 2FA device found. Please refresh and try again.')
            return redirect('two_factor_setup')

        # Verify the token
        backup_codes = confirm_totp_setup(user, device, token)
        if backup_codes is not None:
            # Store the backup codes in the session to show once
            request.session['new_backup_codes'] = backup_codes

            return redirect('two_factor_backup_codes_reveal')
        else:
            messages.error(request, 'Invalid verification code. Please try again.')

    # Create a new device if none exists
    if not device:
        device = pending_totp_device(user)

    context = {
        'qr_code_url': '/accounts/two-factor/qrcode/',
        # 11-06-26: authenticator apps take the key in base32 (what the QR's
        # otpauth URL carries); `device.key` is hex, so typing it in failed.
        'manual_entry_key': manual_entry_key(device),
        'account_name': user.username,
    }

    return render(request, 'two_factor/setup.html', context)


@login_required
def two_factor_backup_codes_reveal(request):
    """
    Show newly-generated backup codes once after setup or regeneration.
    Codes come from the session so they are only shown once.
    """
    backup_codes = request.session.pop('new_backup_codes', None)
    if not backup_codes:
        # No codes in session — redirect to profile
        return redirect('profile')

    # Mark that the user has seen their backup codes
    request.user.backup_codes_acknowledged = True
    request.user.save(update_fields=['backup_codes_acknowledged'])

    # Bust the 2FA status cache so the warning banner disappears immediately
    from django.core.cache import cache
    cache.delete(f'2fa_status_{request.user.pk}')

    return render(request, 'two_factor/backup_codes_reveal.html', {
        'backup_codes': backup_codes,
    })


@login_required
def two_factor_regenerate_backup_codes(request):
    """
    Regenerate backup codes — destroys existing codes and issues new ones.
    Requires POST to prevent accidental regeneration.
    """
    if not user_has_device(request.user):
        messages.error(request, 'You must have 2FA enabled to manage backup codes.')
        return redirect('two_factor_setup')

    if request.method == 'POST':
        backup_codes = regenerate_backup_codes(request.user)
        request.session['new_backup_codes'] = backup_codes
        return redirect('two_factor_backup_codes_reveal')

    # GET: show confirmation page
    backup_device = _get_backup_device(request.user)
    remaining = backup_device.token_set.count() if backup_device else 0
    return render(request, 'two_factor/regenerate_backup_codes.html', {
        'remaining_codes': remaining,
    })


@login_required
def two_factor_qrcode(request):
    """
    Generate QR code for 2FA setup
    """
    # Get unconfirmed device
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()

    if not device:
        return HttpResponse('No device found', status=404)

    # Generate QR code
    img = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage)
    stream = io.BytesIO()
    img.save(stream)

    return HttpResponse(stream.getvalue(), content_type='image/svg+xml')


def check_second_factor(user, token):
    """
    Check a TOTP or backup code for ``user``. Shared by the web verify step
    below and the mobile app's ``auth/verify/``.

    Returns ``(device, kind, remaining_backup_codes)`` on success — ``kind``
    is ``'totp'`` or ``'backup'`` and a backup code is DELETED here (one-time
    use) — or ``(None, None, None)``. ``token`` should already be stripped and
    upper-cased. TOTP is tried first; a backup code only if TOTP didn't match
    and the token is 8 characters, as it always was.
    """
    totp_device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    backup_device = _get_backup_device(user)

    if totp_device and totp_device.verify_token(token):
        return totp_device, 'totp', None
    if backup_device and len(token) == 8:
        static_token = backup_device.token_set.filter(token=token).first()
        if static_token:
            static_token.delete()  # One-time use — delete immediately
            remaining = backup_device.token_set.count()
            logger.warning(
                f"User {user.username} used a 2FA backup code. "
                f"{remaining} code(s) remaining."
            )
            return backup_device, 'backup', remaining
    return None, None, None


def _record_2fa_failure(request, user=None):
    """
    Track and log failed 2FA verification attempts.
    Sends a critical alert after _2FA_MAX_FAILURES failures in the window.

    ``user`` defaults to ``request.user``; the mobile verify step passes the
    ticket's user (there is no session user there). Same counter either way,
    so web and app failures add up.
    """
    ip_address = get_client_ip(request) or 'unknown'
    user = user or request.user
    fail_key = f'2fa_failures_{user.pk}'
    fail_count = cache.get(fail_key, 0) + 1
    cache.set(fail_key, fail_count, _2FA_WINDOW_SECONDS)

    security_logger.warning(
        f"[2FA] Failed verification for {user.username} from {ip_address} "
        f"(attempt {fail_count} in {_2FA_WINDOW_SECONDS // 60}-minute window)"
    )

    if fail_count >= _2FA_MAX_FAILURES:
        security_logger.critical(
            f"[2FA] BRUTE FORCE: {user.username} failed 2FA {fail_count} times "
            f"from {ip_address} — possible credential stuffing with stolen password"
        )
        try:
            from src.security_notifications import send_security_alert
            send_security_alert(
                event_type='2FA_BRUTE_FORCE',
                severity='critical',
                details=(
                    f"User {user.name} ({user.username}) has failed 2FA verification "
                    f"{fail_count} times in {_2FA_WINDOW_SECONDS // 60} minutes.\n\n"
                    f"This may indicate that the account password has been compromised "
                    f"and an attacker is attempting to bypass 2FA."
                ),
                ip_address=ip_address,
                user=user,
            )
        except Exception as e:
            security_logger.error(f"Failed to send 2FA brute force alert: {e}")

        try:
            from src.models import ActivityLog
            ActivityLog.log_activity(
                action_type='security_alert',
                user=user,
                description=(
                    f"Repeated 2FA failures: {fail_count} failed attempts in "
                    f"{_2FA_WINDOW_SECONDS // 60} minutes from {ip_address}."
                ),
                ip_address=ip_address,
                metadata={'severity': 'critical', 'fail_count': fail_count},
            )
        except Exception as e:
            security_logger.error(f"Failed to write 2FA brute force ActivityLog: {e}")


@login_required
def two_factor_verify(request):
    """
    Verify 2FA token during login — accepts TOTP codes or 8-character backup codes.
    """
    # Check if user has 2FA device
    if not user_has_device(request.user):
        messages.error(request, 'Two-Factor Authentication is not set up for your account.')
        return redirect('two_factor_setup')

    from src.next_url import safe_next
    next_url = safe_next(request)   # v3.37.0: set by Enforce2FAMiddleware

    # Check if already verified
    if request.user.is_verified():
        return redirect(next_url or 'home')

    if request.method == 'POST':
        token = request.POST.get('token', '').strip().upper()

        totp_device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()

        verified = False

        # TOTP first (6-digit numeric), then a backup code (8-char hex)
        device, kind, remaining = check_second_factor(request.user, token)
        if device is not None:
            otp_login(request, device)
            verified = True
            if kind == 'backup':
                if remaining <= 2:
                    messages.warning(
                        request,
                        f'Backup code accepted. You only have {remaining} backup code(s) left — '
                        f'consider regenerating them in your profile.'
                    )
                else:
                    messages.info(request, f'Backup code accepted. {remaining} backup code(s) remaining.')

        if verified:
            # Clear failure counter on success
            cache.delete(f'2fa_failures_{request.user.pk}')
            if not token or len(token) != 8:  # Only show generic success for TOTP
                messages.success(request, 'Two-Factor Authentication verified successfully!')
            response = redirect(next_url or 'home')
            # Set remember cookie if requested (only for TOTP, not backup codes)
            remember = request.POST.get('remember_device') == 'on'
            if remember and totp_device and len(token) != 8:
                set_remember_cookie(response, request.user, totp_device)
                logger.info(f'User {request.user.username} enabled 2FA device remembering.')
            return response
        else:
            messages.error(request, 'Invalid verification code. Please try again.')
            _record_2fa_failure(request)

    backup_device = _get_backup_device(request.user)
    has_passkeys = WebAuthnCredential.objects.filter(user=request.user).exists()
    return render(request, 'two_factor/verify.html', {
        'has_backup_codes': backup_device is not None and backup_device.token_set.exists(),
        'remember_days': _REMEMBER_DAYS,
        'has_passkeys': has_passkeys,
    })


@login_required
def two_factor_disable(request):
    """
    Disable Two-Factor Authentication for the current user.

    Requires password re-authentication (v3.13.2, specced in v3.5.1): a
    stolen/unattended session must not be able to strip 2FA — that's exactly
    the attacker 2FA exists to stop. Attempts are rate-limited (5 per 15 min
    per user) so this endpoint can't be used to brute-force the password.
    """
    if request.method == 'POST':
        ip_address = get_client_ip(request)
        # Shared credential-change bucket (v3.14.0 review fix): 2FA disable,
        # passkey register, and passkey delete draw from ONE failure budget.
        attempts_key = f'cred_change_attempts_{request.user.pk}'
        failures = cache.get(attempts_key, 0)

        if failures >= _2FA_MAX_FAILURES:
            security_logger.warning(
                f'2FA disable rate-limited for {request.user.username} from {ip_address} '
                f'({failures} failed password attempts)'
            )
            messages.error(request, 'Too many failed attempts. Try again in 15 minutes.')
            return render(request, 'two_factor/disable.html', status=429)

        password = request.POST.get('password', '')
        if not password or not request.user.check_password(password):
            cache.set(attempts_key, failures + 1, _2FA_WINDOW_SECONDS)
            security_logger.warning(
                f'2FA disable denied (bad password) for {request.user.username} from {ip_address}'
            )
            messages.error(request, 'Incorrect password. Two-Factor Authentication was NOT disabled.')
            return render(request, 'two_factor/disable.html', status=403)

        cache.delete(attempts_key)

        # Delete all devices for this user (TOTP + backup codes), log, notify
        disable_two_factor(request.user, ip_address)

        messages.success(request, 'Two-Factor Authentication has been disabled.')
        response = redirect('profile')
        clear_remember_cookie(response)
        return response

    return render(request, 'two_factor/disable.html')


@login_required
def two_factor_dismiss(request):
    """
    Dismiss the 2FA setup prompt for 1 hour.
    User can continue using the site without setting up 2FA temporarily.
    """
    # Set dismissal to expire in 1 hour
    dismiss_until = timezone.now() + timedelta(hours=1)
    request.session['2fa_setup_dismissed_until'] = dismiss_until.isoformat()

    messages.info(request, 'Two-Factor Authentication setup has been postponed for 1 hour.')
    return redirect('home')


@login_required
def two_factor_forget_device(request):
    """
    Clear the 'remember this device' cookie for the current browser.
    POST only to prevent accidental clearing via prefetch/crawlers.
    """
    if request.method == 'POST':
        response = redirect('profile')
        clear_remember_cookie(response)
        messages.success(request, 'This device will no longer be remembered for 2FA.')
        return response
    return redirect('profile')
