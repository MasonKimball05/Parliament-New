from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from src.utils.security_utils import get_client_ip
from src.models import ActivityLog

@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)  # Keeps the user logged in
            ActivityLog.log_activity(
                action_type='password_changed',
                user=request.user,
                description=f'{request.user.name} changed their password',
                request=request,
                object_type='ParliamentUser',
                object_id=request.user.pk,
                object_repr=request.user.name,
            )
            messages.success(request, "Password changed successfully.")
            try:
                watch_flag = getattr(request.user, 'watch_flag', None)
                if watch_flag and watch_flag.is_active:
                    from src.security_notifications import send_watch_flag_password_change_alert
                    send_watch_flag_password_change_alert(
                        watched_user=request.user,
                        changed_by_user=request.user,
                        ip_address=get_client_ip(request) or 'unknown',
                        watch_reason=watch_flag.reason,
                    )
            except Exception:
                pass
            return redirect('profile')
        else:
            messages.error(request, "Please correct the error below.")
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'change_password.html', {'form': form})
