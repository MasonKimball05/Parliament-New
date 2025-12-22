from django.shortcuts import redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from src.models import Event

@login_required
@user_passes_test(lambda u: u.is_admin)
def archive_event(request, event_id):
    """Archive an event - admin only"""
    event = get_object_or_404(Event, id=event_id)

    event.archived = True
    event.is_active = False
    event.save()

    messages.success(request, f'Event "{event.title}" has been archived.')
    return redirect('manage_events')


@login_required
@user_passes_test(lambda u: u.is_admin)
def unarchive_event(request, event_id):
    """Unarchive an event - admin only"""
    event = get_object_or_404(Event, id=event_id)

    event.archived = False
    event.is_active = True
    event.save()

    messages.success(request, f'Event "{event.title}" has been unarchived and is now active.')
    return redirect('view_archived_events')
