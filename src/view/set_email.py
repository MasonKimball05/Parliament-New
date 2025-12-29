"""
View for setting user email address
"""
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
import logging

logger = logging.getLogger(__name__)


@login_required
@require_POST
def set_email(request):
    """
    Allow users to set their email address
    """
    email = request.POST.get('email', '').strip()

    if not email:
        messages.error(request, 'Please provide an email address.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    try:
        # Update user's email
        request.user.email = email
        request.user.save()

        messages.success(request, f'Email address set to {email}')
        logger.info(f"User {request.user.username} set email to {email}")

        # Redirect back to where they came from, or home
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    except Exception as e:
        logger.error(f"Error setting email for {request.user.username}: {str(e)}")
        messages.error(request, 'Failed to set email address. Please try again.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))
