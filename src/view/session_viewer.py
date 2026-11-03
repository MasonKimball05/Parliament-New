"""
Session Viewer - View and manage active sessions
Allows users to see all their active sessions and log out remotely.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone
import logging

from ..models import UserSession

logger = logging.getLogger('function_calls')
security_logger = logging.getLogger('admin_actions')


def sessions_for(user, current_session_key=None):
    """
    The member's browser sessions, expired ones swept first, the current one
    (if any) first and then most recently active. Shared with the app
    (src/mobile/views_account_security.py), which has no current one.
    """
    # Cleanup expired sessions first
    UserSession.cleanup_expired_sessions()

    # Get all sessions for the user
    sessions = UserSession.objects.filter(user=user)

    # Mark the current session
    for session in sessions:
        session.is_current = (session.session_key == current_session_key)

    # Sort to put current session first
    return sorted(sessions, key=lambda s: (not s.is_current, -s.last_activity.timestamp()))


def end_session(user, user_session):
    """Log one of ``user``'s sessions out: the Django session and its display row."""
    # Delete the Django session
    try:
        UserSession.delete_session_from_store(user_session.session_key)
    except Exception as e:
        logger.warning(f"Could not delete Django session {user_session.session_key}: {e}")

    # Delete the user session record
    ip_address = user_session.ip_address
    device = user_session.device_type
    user_session.delete()

    security_logger.info(
        f"SESSION REVOKED: User '{user.name}' revoked session from {device} ({ip_address})"
    )


def end_other_sessions(user, keep_session_key=None, keep_mobile_device_id=None):
    """The "log out all other sessions" button (app installs too); returns how many browser sessions ended."""
    # v3.27.0 — shared with change_password / forced_password_change, so a
    # password change gets the exact same "other devices are actually logged
    # out now" behaviour as clicking this button, rather than a weaker,
    # separately-maintained copy of it. See UserSession.revoke_other_sessions.
    count = UserSession.revoke_other_sessions(user, keep_session_key=keep_session_key,
                                              keep_mobile_device_id=keep_mobile_device_id)
    if count:
        security_logger.info(
            f"ALL SESSIONS REVOKED: User '{user.name}' revoked {count} other sessions"
        )
    return count


@login_required
def session_list(request):
    """Display all active sessions for the current user."""
    sessions = sessions_for(request.user, request.session.session_key)

    # Mobile plan, Phase 1: signed-in app installs. A web-view session is
    # also listed above as a session; this is the device behind it.
    from src.mobile.views_devices import live_devices
    current_device_id = request.session.get('mobile_device_id')
    mobile_devices = list(live_devices(request.user)[:50])
    for device in mobile_devices:
        device.is_current = device.pk == current_device_id

    context = {
        'sessions': sessions,
        'session_count': len(sessions),
        'mobile_devices': mobile_devices,
    }

    return render(request, 'account/sessions.html', context)


@login_required
def revoke_session(request, session_key):
    """Revoke (log out) a specific session."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    current_session_key = request.session.session_key

    # Don't allow revoking the current session through this endpoint
    if session_key == current_session_key:
        return JsonResponse({
            'status': 'error',
            'message': 'Cannot revoke your current session. Use logout instead.'
        }, status=400)

    try:
        # Find the user session
        user_session = UserSession.objects.get(
            session_key=session_key,
            user=request.user
        )
    except UserSession.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Session not found.'
        }, status=404)

    end_session(request.user, user_session)
    return JsonResponse({
        'status': 'success',
        'message': 'Session has been logged out.'
    })


@login_required
def revoke_all_other_sessions(request):
    """Revoke all sessions except the current one."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    count = end_other_sessions(request.user, keep_session_key=request.session.session_key)

    if count == 0:
        return JsonResponse({
            'status': 'success',
            'message': 'No other sessions to revoke.',
            'revoked_count': 0
        })

    return JsonResponse({
        'status': 'success',
        'message': f'Successfully logged out of {count} other session{"s" if count != 1 else ""}.',
        'revoked_count': count
    })


@login_required
@require_POST
def revoke_mobile_device(request, public_id):
    """Sign one of your app installs out (form POST, CSRF-protected). 404 for anyone else's."""
    from src.models.mobile import MobileDevice
    device = get_object_or_404(
        MobileDevice, public_id=public_id, user=request.user, revoked_at__isnull=True)
    device.revoke('signed out from the website')
    security_logger.info(
        f"MOBILE DEVICE REVOKED: User '{request.user.name}' signed out app device {device.pk}")
    messages.success(request, 'That device has been signed out of the app.')
    return redirect('session_list')
