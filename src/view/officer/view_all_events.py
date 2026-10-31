from src.decorators import officer_or_advisor_required
from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from src.models import Event
from src.models.users import member_defer


def all_events_querysets(now=None):
    """
    (upcoming, past): every event that isn't archived, upcoming soonest first
    and past newest first. Shared with the app (src/mobile/views_officer_events.py).
    """
    now = now or timezone.now()
    base = Event.objects.filter(archived=False).select_related('created_by').defer(*member_defer('created_by'))
    upcoming_events = base.filter(date_time__gte=now).order_by('date_time')
    past_events = base.filter(date_time__lt=now).order_by('-date_time')
    return upcoming_events, past_events


@login_required
@officer_or_advisor_required
def view_all_events(request):
    """View all events (past and upcoming) for officers"""
    upcoming_events, past_events = all_events_querysets()

    context = {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
    }

    return render(request, 'officer/view_all_events.html', context)
