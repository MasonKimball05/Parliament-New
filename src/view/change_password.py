from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages
from django.core.cache import cache
from src.utils.security_utils import get_client_ip
from src.models import ActivityLog, UserSession

_PW_CHANGE_LIMIT = 5       # attempts
_PW_CHANGE_WINDOW = 3600   # 1 hour


@login_required
def change_password(request):
    if request.method == 'POST':
        # Rate limit: 5 attempts per hour per user
        rate_key = f'pw_change_{request.user.pk}'
        attempts = cache.get(rate_key, 0)
        if attempts >= _PW_CHANGE_LIMIT:
            messages.error(request, 'Too many password change attempts. Please wait an hour and try again.')
            return redirect('profile')
        cache.set(rate_key, attempts + 1, _PW_CHANGE_WINDOW)
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)  # Keeps the user logged in
            # v3.27.0 — log every OTHER device out immediately rather than
            # leaving it to each one's next request (Django already rejects
            # those requests via the session-auth-hash check; this makes the
            # Active Sessions list agree with reality right away too, and
            # closes the actual session rather than just declining to trust
            # it later). See UserSession.revoke_other_sessions.
            UserSession.revoke_other_sessions(
                request.user, keep_session_key=request.session.session_key
            )
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
