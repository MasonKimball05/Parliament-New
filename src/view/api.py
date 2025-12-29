"""
API endpoints for Parliament system
"""
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from src.notifications import mark_announcement_dismissed
import logging

logger = logging.getLogger(__name__)


@login_required
@require_POST
def dismiss_announcement_api(request, announcement_id):
    """
    API endpoint to mark an announcement as dismissed for the current user
    """
    try:
        success = mark_announcement_dismissed(request.user, announcement_id)

        if success:
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Failed to dismiss announcement'}, status=500)

    except Exception as e:
        logger.error(f"Error in dismiss_announcement_api: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
