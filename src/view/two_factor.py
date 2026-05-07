"""
Two-Factor Authentication setup and verification views
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django.core.cache import cache
from django_otp import user_has_device, login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.util import random_hex
from src.utils.security_utils import get_client_ip
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


def _generate_backup_codes(user):
    """
    Generate a fresh set of backup codes for a user.
    Deletes any existing StaticDevice for this user and creates a new one.
    Returns a list of plaintext code strings (shown to user once).
    """
    # Remove any existing backup code device
    StaticDevice.objects.filter(user=user, name='backup').delete()

    device = StaticDevice.objects.create(user=user, name='backup', confirmed=True)
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        # 8-character alphanumeric, uppercase for readability
        code = secrets.token_hex(4).upper()  # e.g. "A3F2C19D"
        StaticToken.objects.create(device=device, token=code)
        codes.append(code)
    return codes


def _get_backup_device(user):
    """Return the user's confirmed backup StaticDevice, or None."""
    return StaticDevice.objects.filter(user=user, name='backup', confirmed=True).first()


@login_required
def two_factor_setup(request):
    """
    Set up Two-Factor Authentication using TOTP (Time-based One-Time Password)
    Users scan QR code with Google Authenticator, Authy, or similar app
    """
    # Check if user already has 2FA set up
    if user_has_device(request.user):
        messages.info(request, 'Two-Factor Authentication is already set up for your account.')
        return redirect('home')

    # Generate or get device for this user
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()

    if request.method == 'POST':
        token = request.POST.get('token', '').strip()

        if not device:
            messages.error(request, 'No 2FA device found. Please refresh and try again.')
            return redirect('two_factor_setup')

        # Verify the token
        if device.verify_token(token):
            # Mark device as confirmed
            device.confirmed = True
            device.save()

            # Generate backup codes and store them in session to show once
            backup_codes = _generate_backup_codes(request.user)
            request.session['new_backup_codes'] = backup_codes

            return redirect('two_factor_backup_codes_reveal')
        else:
            messages.error(request, 'Invalid verification code. Please try again.')

    # Create a new device if none exists
    if not device:
        device = TOTPDevice.objects.create(
            user=request.user,
            name='default',
            confirmed=False,
            key=random_hex()
        )

    context = {
        'qr_code_url': '/accounts/two-factor/qrcode/',
        'manual_entry_key': device.key,
        'account_name': request.user.username,
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
        backup_codes = _generate_backup_codes(request.user)
        request.session['new_backup_codes'] = backup_codes
        logger.info(f"User {request.user.username} regenerated 2FA backup codes.")
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


def _record_2fa_failure(request):
    """
    Track and log failed 2FA verification attempts.
    Sends a critical alert after _2FA_MAX_FAILURES failures in the window.
    """
    ip_address = get_client_ip(request) or 'unknown'
    user = request.user
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

    # Check if already verified
    if request.user.is_verified():
        return redirect('home')

    if request.method == 'POST':
        token = request.POST.get('token', '').strip().upper()

        totp_device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
        backup_device = _get_backup_device(request.user)

        verified = False

        # Try TOTP first (6-digit numeric)
        if totp_device and totp_device.verify_token(token):
            otp_login(request, totp_device)
            verified = True

        # Try backup code (8-char hex) if TOTP didn't match
        elif backup_device and len(token) == 8:
            static_token = backup_device.token_set.filter(token=token).first()
            if static_token:
                static_token.delete()  # One-time use — delete immediately
                otp_login(request, backup_device)
                verified = True
                remaining = backup_device.token_set.count()
                logger.warning(
                    f"User {request.user.username} used a 2FA backup code. "
                    f"{remaining} code(s) remaining."
                )
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
            return redirect('home')
        else:
            messages.error(request, 'Invalid verification code. Please try again.')
            _record_2fa_failure(request)

    backup_device = _get_backup_device(request.user)
    return render(request, 'two_factor/verify.html', {
        'has_backup_codes': backup_device is not None and backup_device.token_set.exists(),
    })


@login_required
def two_factor_disable(request):
    """
    Disable Two-Factor Authentication for the current user
    """
    if request.method == 'POST':
        # Delete all devices for this user (TOTP + backup codes)
        TOTPDevice.objects.filter(user=request.user).delete()
        StaticDevice.objects.filter(user=request.user, name='backup').delete()
        messages.success(request, 'Two-Factor Authentication has been disabled.')
        return redirect('profile')

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
