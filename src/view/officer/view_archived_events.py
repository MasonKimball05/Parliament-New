from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import Event

@login_required
@user_passes_test(lambda u: u.is_admin)
def view_archived_events(request):
    """View archived events - admin only for record keeping"""

    # Get all archived events
    archived_events = Event.objects.filter(
        archived=True
    ).select_related('created_by').order_by('-date_time')

    context = {
        'archived_events': archived_events,
    }

    return render(request, 'officer/view_archived_events.html', context)
