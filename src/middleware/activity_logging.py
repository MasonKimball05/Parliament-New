"""
Login/logout ActivityLog signal receivers.

⚠️ THIS FILE CONTAINS NO MIDDLEWARE, despite its name and its home in
`src/middleware/`. It is three `django.contrib.auth` signal receivers, and it
is LIVE — loaded by `SrcConfig.ready()` (`src/apps.py:43`), which is the only
thing that references it. It is deliberately absent from `MIDDLEWARE` and from
this package's `__init__`, because it is not a middleware.

Recorded because the 08-06-26 review flagged this file as dead code on exactly
that evidence — not in `MIDDLEWARE`, not exported by the package, no importer
under any name resembling a middleware — and was wrong. Every one of those
observations was true; the conclusion did not follow, because the file is
misfiled rather than unused. If it is ever moved to `src/signals.py` (where it
belongs), `apps.py:43` must move with it.
"""
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
