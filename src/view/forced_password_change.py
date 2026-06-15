"""
View for handling forced password changes after admin resets
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from src.forms import ForcedPasswordChangeForm
from django.core.exceptions import ValidationError
from src.models import ActivityLog


@login_required
def forced_password_change(request):
    """
    Force user to change password if force_password_change flag is set.
    This view should be accessed through middleware redirect.
    """
    # If user doesn't need to change password, redirect to home
    if not request.user.force_password_change:
        return redirect('home')

    if request.method == 'POST':
        form = ForcedPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            try:
                form.save()
                # Keep user logged in after password change
                update_session_auth_hash(request, request.user)
                ActivityLog.log_activity(
                    action_type='password_changed',
                    user=request.user,
                    description=f'{request.user.name} completed a forced password change (admin-initiated reset)',
                    request=request,
                    object_type='ParliamentUser',
                    object_id=request.user.pk,
                    object_repr=request.user.name,
                    metadata={'forced': True},
                )
                if getattr(request.user, 'is_pledge', False):
                    ActivityLog.log_activity(
                        action_type='pledge_password_changed',
                        user=request.user,
                        description=f'Pledge {request.user.name} changed their password for the first time',
                        request=request,
                        object_type='ParliamentUser',
                        object_id=request.user.pk,
                        object_repr=request.user.name,
                    )
                messages.success(
                    request,
                    'Password set successfully!'
                )
                try:
                    watch_flag = getattr(request.user, 'watch_flag', None)
                    if watch_flag and watch_flag.is_active:
                        from src.security_notifications import send_watch_flag_password_change_alert
                        from src.utils.security_utils import get_client_ip
                        send_watch_flag_password_change_alert(
                            watched_user=request.user,
                            changed_by_user=request.user,
                            ip_address=get_client_ip(request) or 'unknown',
                            watch_reason=watch_flag.reason,
                        )
                except Exception:
                    pass
                if request.session.get('in_onboarding'):
                    return redirect('/onboarding/?step=passkey')
                return redirect('home')
            except ValidationError as e:
                # Display password validation errors
                for error in e.messages:
                    messages.error(request, error)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ForcedPasswordChangeForm(user=request.user)

    return render(request, 'forced_password_change.html', {
        'form': form,
        'user': request.user
    })
