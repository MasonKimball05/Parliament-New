from src.decorators import admin_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import Event
from src.models.users import member_defer


def archived_events_queryset():
    """Every archived event, newest first. Shared with the app (src/mobile/views_officer_events.py)."""
    return Event.objects.filter(
        archived=True
    ).select_related('created_by').defer(*member_defer('created_by')).order_by('-date_time')


@login_required
@admin_required
def view_archived_events(request):
    """View archived events - admin only for record keeping"""

    # Get all archived events
    archived_events = archived_events_queryset()

    context = {
        'archived_events': archived_events,
    }

    return render(request, 'officer/view_archived_events.html', context)
