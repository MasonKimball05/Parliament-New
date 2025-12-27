from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from src.models import Event
from src.forms import EventForm
from src.decorators import officer_required

@login_required
@officer_required
def manage_events(request):
    """View for officers to manage all events"""
    events = Event.objects.all().order_by('date_time')
    now = timezone.now()

    context = {
        'events': events,
        'current_time': now,
    }
    return render(request, 'officer/manage_events.html', context)

@login_required
@officer_required
def create_event(request):
    """View for officers to create a new event"""
    if request.method == 'POST':
        form = EventForm(request.POST)
        if form.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            event.save()
            return redirect('manage_events')
    else:
        form = EventForm()

    return render(request, 'officer/create_event.html', {'form': form})

@login_required
@officer_required
def edit_event(request, event_id):
    """View for officers to edit an existing event"""
    event = get_object_or_404(Event, pk=event_id)

    if request.method == 'POST':
        form = EventForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            return redirect('manage_events')
    else:
        form = EventForm(instance=event)

    return render(request, 'officer/edit_event.html', {'form': form, 'event': event})

@login_required
@officer_required
def delete_event(request, event_id):
    """View for officers to delete an event"""
    event = get_object_or_404(Event, pk=event_id)

    if request.method == 'POST':
        event.delete()
        return redirect('manage_events')

    return render(request, 'officer/delete_event.html', {'event': event})
