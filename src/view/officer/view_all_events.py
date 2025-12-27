from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from src.models import Event

@login_required
@officer_or_advisor_required
def view_all_events(request):
    """View all events (past and upcoming) for officers"""
    now = timezone.now()

    # Get upcoming events (exclude archived)
    upcoming_events = Event.objects.filter(
        date_time__gte=now,
        archived=False
    ).select_related('created_by').order_by('date_time')

    # Get past events (exclude archived)
    past_events = Event.objects.filter(
        date_time__lt=now,
        archived=False
    ).select_related('created_by').order_by('-date_time')

    context = {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
    }

    return render(request, 'officer/view_all_events.html', context)
