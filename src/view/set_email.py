"""
Views for setting and verifying a user's email address.

First-time email set (user.email is None/empty) → saved immediately.
Changing an existing email → sends a confirmation link to the new address;
the change is not applied until the link is clicked.
"""
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET
from django.core.cache import cache
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)

_RATE_LIMIT = 3          # max verification emails per window
_RATE_WINDOW = 3600      # 1 hour in seconds
_TOKEN_TTL = 86400       # 24 hours in seconds


@login_required
@require_POST
def set_email(request):
    """
    Handle email set / change requests.

    - No existing email: save immediately (first-time setup, no risk).
    - Existing email: send a confirmation link to the new address and hold
      the change until confirmed.
    """
    new_email = request.POST.get('email', '').strip().lower()

    if not new_email:
        messages.error(request, 'Please provide an email address.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    user = request.user

    # -------------------------------------------------------------------------
    # First-time set — save immediately, no verification needed
    # -------------------------------------------------------------------------
    if not user.email:
        user.email = new_email
        user.email_flagged = False
        user.email_flagged_reason = ''
        user.email_flagged_at = None
        user.save(update_fields=['email', 'email_flagged', 'email_flagged_reason', 'email_flagged_at'])
        logger.info(f"[set_email] First-time email set for {user.username}: {new_email}")
        messages.success(request, f'Email address set to {new_email}.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    # -------------------------------------------------------------------------
    # Changing an existing email — require confirmation
    # -------------------------------------------------------------------------

    # No-op if they submitted the same address they already have
    if new_email == user.email.lower():
        messages.info(request, 'That is already your current email address.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    # Check the new address isn't already taken by another user
    from src.models import ParliamentUser
    if ParliamentUser.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        messages.error(request, 'That email address is already in use by another account.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    # Rate limit — prevent spamming verification emails
    rate_key = f'email_verify_rate_{user.pk}'
    attempts = cache.get(rate_key, 0)
    if attempts >= _RATE_LIMIT:
        messages.error(request, 'Too many email change requests. Please wait an hour before trying again.')
        logger.warning(f"[set_email] Rate limit hit for {user.username}")
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    # Invalidate any previous pending token for this user
    from src.models import EmailVerificationToken
    EmailVerificationToken.objects.filter(user=user, used=False).delete()

    # Create new token
    token = EmailVerificationToken.objects.create(
        user=user,
        new_email=new_email,
        expires_at=timezone.now() + timezone.timedelta(seconds=_TOKEN_TTL),
    )

    # Build confirmation URL
    from src.security_notifications import get_site_url
    confirm_url = f"{get_site_url()}/set-email/confirm/{token.token}/"

    # Send confirmation email to the NEW address
    try:
        send_mail(
            subject='[Parliament] Confirm your new email address',
            message=(
                f"Hi {user.get_display_name()},\n\n"
                f"A request was made to change the email address on your Parliament account "
                f"to this address ({new_email}).\n\n"
                f"Click the link below to confirm the change:\n\n"
                f"{confirm_url}\n\n"
                f"This link expires in 24 hours. If you did not request this change, "
                f"you can safely ignore this email — your current address will remain unchanged.\n\n"
                f"— Alpha Mu Parliament"
            ),
            html_message=_render_confirmation_email(user, new_email, confirm_url),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[new_email],
            fail_silently=False,
        )
        cache.set(rate_key, attempts + 1, _RATE_WINDOW)
        logger.info(f"[set_email] Verification email sent to {new_email} for {user.username}")
        messages.success(
            request,
            f'A confirmation link has been sent to {new_email}. '
            f'Click it to complete the change. The link expires in 24 hours.'
        )
    except Exception as exc:
        logger.error(f"[set_email] Failed to send verification email to {new_email}: {exc}")
        token.delete()
        messages.error(request, 'Could not send the confirmation email. Please try again later.')

    return redirect(request.META.get('HTTP_REFERER', 'home'))


@login_required
@require_GET
def confirm_email_change(request, token):
    """
    Apply a pending email change after the user clicks the confirmation link.
    """
    from src.models import EmailVerificationToken

    try:
        pending = EmailVerificationToken.objects.get(token=token, user=request.user)
    except EmailVerificationToken.DoesNotExist:
        messages.error(request, 'This confirmation link is invalid or does not belong to your account.')
        return redirect('home')

    if not pending.is_valid:
        messages.error(
            request,
            'This confirmation link has already been used or has expired. '
            'Please request a new one from your profile settings.'
        )
        return redirect('home')

    # Check the new address is still available (someone else may have claimed it)
    from src.models import ParliamentUser
    if ParliamentUser.objects.filter(email__iexact=pending.new_email).exclude(pk=request.user.pk).exists():
        pending.used = True
        pending.save(update_fields=['used'])
        messages.error(request, 'That email address has since been taken by another account. Please try again with a different address.')
        return redirect('home')

    old_email = request.user.email
    request.user.email = pending.new_email
    request.user.email_flagged = False
    request.user.email_flagged_reason = ''
    request.user.email_flagged_at = None
    request.user.save(update_fields=['email', 'email_flagged', 'email_flagged_reason', 'email_flagged_at'])

    pending.used = True
    pending.save(update_fields=['used'])

    logger.info(f"[set_email] Email confirmed for {request.user.username}: {old_email} → {pending.new_email}")
    messages.success(request, f'Your email address has been updated to {pending.new_email}.')
    return redirect('home')


def _render_confirmation_email(user, new_email, confirm_url):
    """Return the HTML body for the confirmation email."""
    from src.security_notifications import get_site_url
    site_url = get_site_url()
    display_name = user.get_display_name() if hasattr(user, 'get_display_name') else user.username
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Confirm Email Change</title>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); color: white; padding: 30px 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 30px 20px; border-left: 1px solid #e5e7eb; border-right: 1px solid #e5e7eb; }}
        .info-box {{ background: #eff6ff; border-left: 4px solid #3b82f6; padding: 15px; margin: 20px 0; border-radius: 4px; }}
        .btn {{ display: inline-block; padding: 14px 28px; background: #2563eb; color: white !important; text-decoration: none; border-radius: 6px; font-weight: 600; margin: 20px 0; }}
        .footer {{ background: #f3f4f6; padding: 20px; text-align: center; color: #6b7280; font-size: 14px; border-radius: 0 0 8px 8px; border: 1px solid #e5e7eb; }}
        .url-fallback {{ word-break: break-all; font-size: 12px; color: #6b7280; margin-top: 8px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1 style="margin: 0; font-size: 24px;">&#9993; Confirm Email Change</h1>
        <p style="margin: 8px 0 0 0; opacity: 0.9;">Alpha Mu Parliament</p>
    </div>
    <div class="content">
        <p>Hi {display_name},</p>
        <p>A request was made to change the email address on your Parliament account to <strong>{new_email}</strong>.</p>
        <div style="text-align: center;">
            <a href="{confirm_url}" class="btn">Confirm Email Change</a>
            <p class="url-fallback">Or copy and paste this link: {confirm_url}</p>
        </div>
        <div class="info-box">
            <strong>This link expires in 24 hours</strong> and can only be used once.
            Your email address will not change until you click it.
        </div>
        <p style="color: #6b7280; font-size: 14px;">
            If you did not request this change, you can safely ignore this email.
            Your current email address will remain unchanged.
        </p>
    </div>
    <div class="footer">
        <p>This is an automated email from the Alpha Mu Parliament system.</p>
        <p><a href="{site_url}" style="color: #3b82f6;">Alpha Mu Parliament</a></p>
    </div>
</body>
</html>"""
