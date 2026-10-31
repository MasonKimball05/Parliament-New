from src.decorators import admin_required
from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from src.models import Event


def set_event_archived(event, archived):
    """
    Archive (and hide) or unarchive (and show) an event. Shared with the app
    (src/mobile/views_officer_events.py).
    """
    event.archived = archived
    event.is_active = not archived
    event.save()


@login_required
@admin_required
@require_POST
def archive_event(request, event_id):
    """Archive an event - admin only"""
    event = get_object_or_404(Event, id=event_id)

    set_event_archived(event, True)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'archived': True})
    messages.success(request, f'Event "{event.title}" has been archived.')
    return redirect('manage_events')


@login_required
@admin_required
@require_POST
def unarchive_event(request, event_id):
    """Unarchive an event - admin only"""
    event = get_object_or_404(Event, id=event_id)

    set_event_archived(event, False)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'archived': False})
    messages.success(request, f'Event "{event.title}" has been unarchived and is now active.')
    return redirect('view_archived_events')
