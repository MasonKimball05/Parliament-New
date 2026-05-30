from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from ..models import ParliamentUser, ActivityLog
from django.contrib.auth import login
import logging

security_logger = logging.getLogger('security')
logger = logging.getLogger('function_calls')

SESSION_ORIGINAL_ID   = '_impersonating_original_user_id'
SESSION_ORIGINAL_NAME = '_impersonating_original_user_name'


@staff_member_required
def login_as_view(request, user_id):
    """
    Admin impersonation — logs in as another user for support/debugging.
    Stores the original admin's identity in the session so they can return.
    """
    target = get_object_or_404(ParliamentUser, pk=user_id)

    # Capture original admin info before login() flushes the session
    original_id   = request.user.user_id
    original_name = request.user.get_display_name()
    original_pk   = request.user.pk

    # Perform the login (Django will flush/cycle the session here)
    target.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, target)

    # Restore impersonation tracking into the new session
    request.session[SESSION_ORIGINAL_ID]   = original_id
    request.session[SESSION_ORIGINAL_NAME] = original_name

    # Security log
    security_logger.warning(
        f"ADMIN IMPERSONATION START: {original_name} (ID: {original_id}) "
        f"logged in as {target.username} (ID: {target.user_id})"
    )

    # Activity log
    try:
        ActivityLog.log_activity(
            action_type='login_as_user',
            user=target,
            description=f'{original_name} logged in as {target.get_display_name()}',
            request=request,
            metadata={
                'action': 'impersonation_start',
                'admin_user_id': original_id,
                'target_user_id': target.user_id,
            }
        )
    except Exception:
        pass

    logger.info(f"Impersonation started: admin={original_id} target={target.user_id}")
    return redirect('home')


@login_required
def return_to_original_user(request):
    """
    Returns from an impersonation session back to the original admin account.
    """
    original_id = request.session.get(SESSION_ORIGINAL_ID)
    if not original_id:
        # Not in an impersonation session — just go home
        return redirect('home')

    impersonated_name = request.user.get_display_name()
    impersonated_id   = request.user.user_id

    original_admin = get_object_or_404(ParliamentUser, user_id=original_id)

    # Security log
    security_logger.warning(
        f"ADMIN IMPERSONATION END: {original_admin.get_display_name()} (ID: {original_id}) "
        f"returned from impersonating {impersonated_name} (ID: {impersonated_id})"
    )

    # Perform the login back as the original admin
    original_admin.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, original_admin)

    # Clear impersonation keys from the new session
    request.session.pop(SESSION_ORIGINAL_ID,   None)
    request.session.pop(SESSION_ORIGINAL_NAME, None)

    # Activity log
    try:
        ActivityLog.log_activity(
            action_type='login_as_user',
            user=original_admin,
            description=f'{original_admin.get_display_name()} returned from impersonating {impersonated_name}',
            request=request,
            metadata={
                'action': 'impersonation_end',
                'admin_user_id': original_id,
                'target_user_id': impersonated_id,
            }
        )
    except Exception:
        pass

    logger.info(f"Impersonation ended: admin={original_id} was_impersonating={impersonated_id}")
    return redirect('home')


# Backwards-compatible alias
login_as_user = login_as_view
