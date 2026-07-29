"""
Self-service 2FA recovery flow.

Allows a logged-in user (password authenticated, but no working TOTP) to
request a re-enrollment link via email. The link is time-limited (1 hour)
and invalidated after use by deleting the TOTP device.

Flow:
  1. User on /accounts/two-factor/verify/ clicks "Lost your authenticator?"
  2. GET  /accounts/two-factor/recovery/        → recovery_request form
  3. POST /accounts/two-factor/recovery/        → sends email, shows confirmation
  4. User clicks link in email
  5. GET  /accounts/two-factor/recovery-confirm/<uidb64>/<token>/
     → validates token, deletes TOTP + backup devices, redirects to setup
"""
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp.plugins.otp_static.models import StaticDevice

from src.models import ParliamentUser, ActivityLog
from src.utils.security_utils import get_client_ip
from src.auth_backends import AUTH_BACKEND_PATH

_RECOVERY_LIMIT = 3       # max requests per user per 24 hours
_RECOVERY_WINDOW = 86400  # 24 hours

logger = logging.getLogger(__name__)
security_logger = logging.getLogger('security')


class TwoFactorRecoveryTokenGenerator(PasswordResetTokenGenerator):
    """
    Short-lived HMAC token for 2FA re-enrollment.
    Expires after 1 hour. Distinct from password reset tokens via the
    '2fa_recovery' domain suffix in the hash value.
    """
    timeout = 3600  # 1 hour

    def _make_hash_value(self, user, timestamp):
        login_timestamp = (
            '' if user.last_login is None
            else user.last_login.replace(microsecond=0, tzinfo=None)
        )
        return f'{user.pk}{user.password}{login_timestamp}{timestamp}2fa_recovery'


_recovery_token = TwoFactorRecoveryTokenGenerator()


@login_required
def two_factor_recovery_request(request):
    """
    GET:  Show a page explaining the recovery flow and a "Send recovery email" button.
    POST: Send the recovery email to the authenticated user's email address.
    """
    user = request.user

    if not user.email:
        return render(request, 'two_factor/recovery_request.html', {
            'no_email': True,
        })

    if request.method == 'POST':
        rate_key = f'2fa_recovery_{user.pk}'
        attempts = cache.get(rate_key, 0)
        if attempts >= _RECOVERY_LIMIT:
            return render(request, 'two_factor/recovery_request.html', {
                'rate_limited': True,
                'user_email': user.email,
            })
        cache.set(rate_key, attempts + 1, _RECOVERY_WINDOW)

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = _recovery_token.make_token(user)
        recovery_url = request.build_absolute_uri(
            f'/accounts/two-factor/recovery-confirm/{uid}/{token}/'
        )

        try:
            html_message = render_to_string('emails/two_factor_recovery.html', {
                'user': user,
                'recovery_url': recovery_url,
                'site_url': request.build_absolute_uri('/').rstrip('/'),
            })
            plain_message = (
                f'Hi {user.get_display_name()},\n\n'
                f'Someone (hopefully you) requested a 2FA re-enrollment link for your Parliament account.\n\n'
                f'Click the link below to remove your current authenticator and set up a new one:\n'
                f'{recovery_url}\n\n'
                f'This link expires in 1 hour and can only be used once.\n\n'
                f'If you did not request this, your account may be at risk — contact an administrator immediately.\n\n'
                f'Parliament'
            )
            send_mail(
                subject='Parliament — 2FA Re-enrollment Link',
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(
                f'2FA recovery email sent to {user.username} ({user.email}) '
                f'from IP {get_client_ip(request)}'
            )
            security_logger.info(
                f'[2FA-RECOVERY] Recovery email requested by {user.username} '
                f'from {get_client_ip(request)}'
            )
            ActivityLog.log_activity(
                action_type='other',
                user=user,
                description='Requested 2FA re-enrollment link via self-service recovery.',
                request=request,
            )
        except Exception as e:
            logger.error(f'Failed to send 2FA recovery email to {user.email}: {e}')
            return render(request, 'two_factor/recovery_request.html', {
                'email_error': True,
                'user_email': user.email,
            })

        return render(request, 'two_factor/recovery_request.html', {
            'email_sent': True,
            'user_email': user.email,
        })

    return render(request, 'two_factor/recovery_request.html', {
        'user_email': user.email,
    })


def two_factor_recovery_confirm(request, uidb64, token):
    """
    Validates the recovery token, wipes all 2FA devices for the user,
    and redirects to the 2FA setup page.

    No login_required — the user may be opening the link from a different
    browser or device. The uidb64 identifies the user; the HMAC token
    proves they received the email.
    """
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = ParliamentUser.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, ParliamentUser.DoesNotExist):
        return render(request, 'two_factor/recovery_confirm_invalid.html', status=400)

    if not _recovery_token.check_token(user, token):
        return render(request, 'two_factor/recovery_confirm_invalid.html', status=400)

    # Token is valid — wipe all 2FA devices so the user can re-enroll
    totp_deleted = TOTPDevice.objects.filter(user=user).delete()[0]
    backup_deleted = StaticDevice.objects.filter(user=user, name='backup').delete()[0]

    security_logger.warning(
        f'[2FA-RECOVERY] Self-service recovery completed for {user.username}. '
        f'Deleted {totp_deleted} TOTP device(s) and {backup_deleted} backup device(s).'
    )

    # Notify admins — recovery link use is a potential account-takeover indicator
    try:
        admin_emails = list(
            ParliamentUser.objects.filter(is_staff=True, is_active=True)
            .exclude(email='')
            .values_list('email', flat=True)
        )
        if admin_emails:
            ip = get_client_ip(request) or 'unknown'
            send_mail(
                subject='[Parliament] 2FA Self-Service Recovery Used',
                message=(
                    f'A member has used the 2FA self-service recovery link.\n\n'
                    f'User:     {user.username} ({user.email})\n'
                    f'IP:       {ip}\n\n'
                    f'Their TOTP and backup devices have been removed. '
                    f'They have been redirected to set up a new authenticator.\n\n'
                    f'If this was not initiated by the member themselves, their account may be compromised.'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=admin_emails,
                fail_silently=True,
            )
    except Exception:
        pass

    ActivityLog.log_activity(
        action_type='security_violation',
        user=user,
        description='2FA devices removed via self-service recovery link. User will re-enroll.',
        request=request,
        object_type='TOTPDevice',
    )

    # Log user in (they may be coming from a fresh browser tab via email)
    from django.contrib.auth import login as auth_login
    if not request.user.is_authenticated or request.user.pk != user.pk:
        # Specify the backend so Django doesn't complain about multiple backends
        user.backend = AUTH_BACKEND_PATH
        auth_login(request, user)

    return redirect('two_factor_setup')
