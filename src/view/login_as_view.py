from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect
from ..models import *
from django.contrib.auth import login
import logging

@staff_member_required
def login_as_view(request, user_id):
    """
    SECURITY: Admin impersonation feature - allows staff to login as another user
    for support/debugging purposes. This is logged for audit purposes.
    """
    user = get_object_or_404(ParliamentUser, pk=user_id)

    # Log the impersonation for security audit
    logger = logging.getLogger('security')
    logger.warning(
        f"ADMIN IMPERSONATION: {request.user.username} (ID: {request.user.user_id}) "
        f"logged in as {user.username} (ID: {user.user_id})"
    )

    # Set the backend attribute required by Django's auth system
    user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, user)

    return redirect('home')

# Alias for backwards compatibility
login_as_user = login_as_view