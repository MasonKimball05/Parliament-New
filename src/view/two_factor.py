"""
Two-Factor Authentication setup and verification views
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django_otp import user_has_device, login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.util import random_hex
from datetime import timedelta
import qrcode
import qrcode.image.svg
import io


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
            messages.success(request, 'Two-Factor Authentication has been successfully enabled!')
            return redirect('home')
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

    # Generate QR code data
    config_url = device.config_url

    context = {
        'qr_code_url': '/accounts/two-factor/qrcode/',
        'manual_entry_key': device.key,
        'account_name': request.user.username,
    }

    return render(request, 'two_factor/setup.html', context)


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


@login_required
def two_factor_verify(request):
    """
    Verify 2FA token during login
    """
    # Check if user has 2FA device
    if not user_has_device(request.user):
        messages.error(request, 'Two-Factor Authentication is not set up for your account.')
        return redirect('two_factor_setup')

    # Check if already verified
    if request.user.is_verified():
        return redirect('home')

    if request.method == 'POST':
        token = request.POST.get('token', '').strip()

        # Get user's confirmed device
        device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()

        if device and device.verify_token(token):
            # Mark user as verified for this session using django-otp's login function
            otp_login(request, device)
            messages.success(request, 'Two-Factor Authentication verified successfully!')
            return redirect('home')
        else:
            messages.error(request, 'Invalid verification code. Please try again.')

    return render(request, 'two_factor/verify.html')


@login_required
def two_factor_disable(request):
    """
    Disable Two-Factor Authentication for the current user
    """
    if request.method == 'POST':
        # Delete all devices for this user
        TOTPDevice.objects.filter(user=request.user).delete()
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
