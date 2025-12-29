"""
Middleware for automatic activity logging
"""
from django.utils.deprecation import MiddlewareMixin
from src.models import ActivityLog
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """Log successful user login"""
    ActivityLog.log_activity(
        action_type='login',
        user=user,
        description=f'{user.get_display_name()} logged in successfully',
        request=request
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    """Log user logout"""
    if user:
        ActivityLog.log_activity(
            action_type='logout',
            user=user,
            description=f'{user.get_display_name()} logged out',
            request=request
        )


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    """Log failed login attempts"""
    username = credentials.get('username', 'Unknown')
    ActivityLog.log_activity(
        action_type='login_failed',
        user=None,
        description=f'Failed login attempt for username: {username}',
        request=request
    )
