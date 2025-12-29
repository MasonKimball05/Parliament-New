"""
Member-facing view to submit excuse requests for events
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.core.files.storage import default_storage

from src.models import Event, AttendanceExcuse, ActivityLog


@login_required
def my_excuses(request):
    """
    View member's excuse requests and submit new ones
    """
    # Get member's existing excuse requests
    my_excuse_requests = AttendanceExcuse.objects.filter(
        user=request.user
    ).select_related('event', 'reviewed_by').order_by('-submitted_at')

    # Get upcoming events that allow excuses (regardless of whether attendance is required)
    now = timezone.now()
    available_events = Event.objects.filter(
        allow_excuses=True,
        date_time__gte=now,
        is_active=True
    ).exclude(
        # Exclude events user already has an excuse for
        excuse_requests__user=request.user
    ).order_by('date_time')

    # Filter to only events user can see
    available_events = [event for event in available_events if event.is_visible_to_user(request.user)]

    # Filter to only events where deadline hasn't passed
    available_events = [event for event in available_events if event.can_submit_excuse()]

    context = {
        'my_excuses': my_excuse_requests,
        'available_events': available_events,
    }

    return render(request, 'my_excuses.html', context)


@login_required
def submit_excuse(request, event_id):
    """
    Submit an excuse request for a specific event
    """
    event = get_object_or_404(Event, id=event_id, is_active=True)

    # Check if user can see this event
    if not event.is_visible_to_user(request.user):
        messages.error(request, 'You do not have access to this event.')
        return redirect('my_excuses')

    # Check if excuses are allowed
    if not event.allow_excuses:
        messages.error(request, 'Excuses are not allowed for this event.')
        return redirect('my_excuses')

    # Check if deadline has passed
    if not event.can_submit_excuse():
        messages.error(request, 'The deadline for submitting excuses has passed.')
        return redirect('my_excuses')

    # Check if user already has an excuse for this event
    existing_excuse = AttendanceExcuse.objects.filter(event=event, user=request.user).first()
    if existing_excuse:
        messages.warning(request, 'You have already submitted an excuse for this event.')
        return redirect('my_excuses')

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        supporting_document = request.FILES.get('supporting_document')

        # Validate reason
        if not reason or len(reason) < 10:
            messages.error(request, 'Please provide a detailed reason (at least 10 characters).')
            return render(request, 'submit_excuse.html', {'event': event})

        # Create excuse request
        excuse = AttendanceExcuse.objects.create(
            event=event,
            user=request.user,
            reason=reason,
            supporting_document=supporting_document
        )

        # Log activity
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'Submitted excuse request for {event.title}',
            request=request,
            object_type='AttendanceExcuse',
            object_id=excuse.id,
            object_repr=str(excuse)
        )

        messages.success(request, f'Your excuse request for "{event.title}" has been submitted for review.')
        return redirect('my_excuses')

    context = {
        'event': event,
    }

    return render(request, 'submit_excuse.html', context)


@login_required
def cancel_excuse(request, excuse_id):
    """
    Cancel/delete a pending excuse request
    """
    excuse = get_object_or_404(AttendanceExcuse, id=excuse_id, user=request.user)

    # Can only cancel pending excuses
    if excuse.status != 'pending':
        messages.error(request, 'You can only cancel pending excuse requests.')
        return redirect('my_excuses')

    event_title = excuse.event.title
    excuse.delete()

    # Log activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Cancelled excuse request for {event_title}',
        request=request
    )

    messages.success(request, f'Your excuse request for "{event_title}" has been cancelled.')
    return redirect('my_excuses')
