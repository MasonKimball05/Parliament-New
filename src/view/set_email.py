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
        # Log before save
        logger.info(f"User {request.user.username} (ID: {request.user.user_id}) attempting to set email to: {email}")
        logger.info(f"Current email before save: {request.user.email}")

        # Update user's email - be explicit about which field to update
        request.user.email = email
        request.user.save(update_fields=['email'])

        # Verify the save worked
        request.user.refresh_from_db()
        logger.info(f"Email after save and refresh: {request.user.email}")

        if request.user.email == email:
            messages.success(request, f'Email address successfully set to {email}')
            logger.info(f"✓ Email save verified for user {request.user.username}")
        else:
            logger.error(f"✗ Email save FAILED - Expected: {email}, Got: {request.user.email}")
            messages.warning(request, f'Email may not have saved correctly. Please verify in your profile.')

        # Redirect back to where they came from, or home
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    except Exception as e:
        logger.error(f"Error setting email for {request.user.username}: {str(e)}")
        logger.exception("Full traceback:")
        messages.error(request, 'Failed to set email address. Please try again.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))
