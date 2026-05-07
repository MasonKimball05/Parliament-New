"""
View for setting user email address
"""
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db import connection, transaction
import logging

logger = logging.getLogger(__name__)


@login_required
@require_POST
def set_email(request):
    """
    Allow users to set their email address
    """
    email = request.POST.get('email', '').strip()

    # Enhanced logging - log ALL request info
    logger.info(f"="*80)
    logger.info(f"SET_EMAIL VIEW CALLED")
    logger.info(f"User: {request.user.username} (ID: {request.user.user_id})")
    logger.info(f"Email from POST: '{email}'")
    logger.info(f"Request method: {request.method}")
    logger.info(f"All POST data: {dict(request.POST)}")
    logger.info(f"="*80)

    if not email:
        logger.warning(f"Empty email provided by {request.user.username}")
        messages.error(request, 'Please provide an email address.')
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    try:
        # Log current state
        logger.info(f"BEFORE SAVE:")
        logger.info(f"  - User object email: '{request.user.email}'")
        logger.info(f"  - User object id: {request.user.pk}")

        # Direct database check BEFORE
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM src_parliamentuser WHERE user_id = %s",
                [request.user.user_id]
            )
            db_email_before = cursor.fetchone()
            logger.info(f"  - Database email (direct query): '{db_email_before[0] if db_email_before else 'NULL'}'")

        # Update with transaction to ensure atomicity
        with transaction.atomic():
            request.user.email = email
            # Clear any email deliverability flag when the user sets a new address
            request.user.email_flagged = False
            request.user.email_flagged_reason = ''
            request.user.email_flagged_at = None
            request.user.save(update_fields=['email', 'email_flagged', 'email_flagged_reason', 'email_flagged_at'])
            logger.info(f"AFTER SAVE (in transaction):")
            logger.info(f"  - User object email: '{request.user.email}'")

        # Refresh from database
        request.user.refresh_from_db()
        logger.info(f"AFTER REFRESH:")
        logger.info(f"  - User object email: '{request.user.email}'")

        # Direct database check AFTER
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT email FROM src_parliamentuser WHERE user_id = %s",
                [request.user.user_id]
            )
            db_email_after = cursor.fetchone()
            logger.info(f"  - Database email (direct query): '{db_email_after[0] if db_email_after else 'NULL'}'")

        # Verify
        if request.user.email == email:
            messages.success(request, f'Email address successfully set to {email}')
            logger.info(f"✓ SUCCESS: Email verified as '{email}'")
        else:
            logger.error(f"✗ FAILURE: Expected '{email}', got '{request.user.email}'")
            messages.error(request, f'Email save verification failed. Expected: {email}, Got: {request.user.email}')

        logger.info(f"="*80)
        return redirect(request.META.get('HTTP_REFERER', 'home'))

    except Exception as e:
        logger.error(f"EXCEPTION in set_email:")
        logger.error(f"  - Error: {str(e)}")
        logger.error(f"  - Type: {type(e).__name__}")
        logger.exception("Full traceback:")
        messages.error(request, f'Failed to set email address: {str(e)}')
        logger.info(f"="*80)
        return redirect(request.META.get('HTTP_REFERER', 'home'))
