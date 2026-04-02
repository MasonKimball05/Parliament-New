"""
Session Viewer - View and manage active sessions
Allows users to see all their active sessions and log out remotely.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib import messages
from django.contrib.sessions.models import Session
from django.utils import timezone
import logging

from ..models import UserSession

logger = logging.getLogger('function_calls')
security_logger = logging.getLogger('admin_actions')


@login_required
def session_list(request):
    """Display all active sessions for the current user."""
    current_session_key = request.session.session_key

    # Cleanup expired sessions first
    UserSession.cleanup_expired_sessions()

    # Get all sessions for the user
    sessions = UserSession.objects.filter(user=request.user)

    # Mark the current session
    for session in sessions:
        session.is_current = (session.session_key == current_session_key)

    # Sort to put current session first
    sessions = sorted(sessions, key=lambda s: (not s.is_current, -s.last_activity.timestamp()))

    context = {
        'sessions': sessions,
        'session_count': len(sessions),
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

        # Delete the Django session
        try:
            Session.objects.filter(session_key=session_key).delete()
        except Exception as e:
            logger.warning(f"Could not delete Django session {session_key}: {e}")

        # Delete the user session record
        ip_address = user_session.ip_address
        device = user_session.device_type
        user_session.delete()

        security_logger.info(
            f"SESSION REVOKED: User '{request.user.name}' revoked session from {device} ({ip_address})"
        )

        return JsonResponse({
            'status': 'success',
            'message': 'Session has been logged out.'
        })

    except UserSession.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': 'Session not found.'
        }, status=404)


@login_required
def revoke_all_other_sessions(request):
    """Revoke all sessions except the current one."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    current_session_key = request.session.session_key

    # Get all other sessions
    other_sessions = UserSession.objects.filter(
        user=request.user
    ).exclude(session_key=current_session_key)

    count = other_sessions.count()

    if count == 0:
        return JsonResponse({
            'status': 'success',
            'message': 'No other sessions to revoke.',
            'revoked_count': 0
        })

    # Delete Django sessions
    session_keys = list(other_sessions.values_list('session_key', flat=True))
    Session.objects.filter(session_key__in=session_keys).delete()

    # Delete user session records
    other_sessions.delete()

    security_logger.info(
        f"ALL SESSIONS REVOKED: User '{request.user.name}' revoked {count} other sessions"
    )

    return JsonResponse({
        'status': 'success',
        'message': f'Successfully logged out of {count} other session{"s" if count != 1 else ""}.',
        'revoked_count': count
    })
