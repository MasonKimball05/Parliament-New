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


def count_password_change_attempt(user):
    """
    Rate limit: 5 attempts per hour per user, shared by the website and the
    app (src/mobile/views_account_security.py). Counts this attempt and
    returns False once the limit is reached.
    """
    rate_key = f'pw_change_{user.pk}'
    attempts = cache.get(rate_key, 0)
    if attempts >= _PW_CHANGE_LIMIT:
        return False
    cache.set(rate_key, attempts + 1, _PW_CHANGE_WINDOW)
    return True


def after_password_change(request, user, keep_session_key=None, keep_mobile_device_id=None):
    """
    Everything a successful password change does besides saving the hash,
    for the website and the app alike. ``request`` is the Django request.

    v3.27.0 — log every OTHER device out immediately rather than leaving it to
    each one's next request (Django already rejects those requests via the
    session-auth-hash check; this makes the Active Sessions list agree with
    reality right away too, and closes the actual session rather than just
    declining to trust it later). See UserSession.revoke_other_sessions. The
    app keeps its own install signed in (``keep_mobile_device_id``), as the
    website keeps its own session.
    """
    UserSession.revoke_other_sessions(
        user, keep_session_key=keep_session_key, keep_mobile_device_id=keep_mobile_device_id,
    )
    ActivityLog.log_activity(
        action_type='password_changed',
        user=user,
        description=f'{user.name} changed their password',
        request=request,
        object_type='ParliamentUser',
        object_id=user.pk,
        object_repr=user.name,
    )
    try:
        watch_flag = getattr(user, 'watch_flag', None)
        if watch_flag and watch_flag.is_active:
            from src.security_notifications import send_watch_flag_password_change_alert
            send_watch_flag_password_change_alert(
                watched_user=user,
                changed_by_user=user,
                ip_address=get_client_ip(request) or 'unknown',
                watch_reason=watch_flag.reason,
            )
    except Exception:  # nosec B110  # the alert is best-effort; the change itself already succeeded
        pass


@login_required
def change_password(request):
    if request.method == 'POST':
        if not count_password_change_attempt(request.user):
            messages.error(request, 'Too many password change attempts. Please wait an hour and try again.')
            return redirect('profile')
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)  # Keeps the user logged in
            after_password_change(request, request.user, keep_session_key=request.session.session_key)
            messages.success(request, "Password changed successfully.")
            return redirect('profile')
        else:
            messages.error(request, "Please correct the error below.")
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'change_password.html', {'form': form})
