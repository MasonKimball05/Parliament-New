from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db import models
from django.db.models import Count, Q
from django.core.paginator import Paginator
from django.contrib import messages
from datetime import timedelta
from dateutil.relativedelta import relativedelta
import base64
from src.models import Event, EventReminderRecipient
from src.forms import EventForm
from src.decorators import officer_required
from src.notification_service import notify_all_active_members
from src.models.users import member_defer

@login_required
@officer_required
def manage_events(request):
    """View for officers to manage all events"""
    now = timezone.now()

    # Get filter from query params
    show_filter = request.GET.get('filter', 'upcoming')
    series_id = request.GET.get('series')  # Filter by specific series

    # Base queryset - show ALL events including recurring instances
    if series_id:
        # Show all instances of a specific series
        try:
            parent_event = Event.objects.get(pk=series_id, is_recurring=True)
            events = Event.objects.filter(
                archived=False
            ).filter(
                models.Q(pk=series_id) | models.Q(parent_event_id=series_id)
            ).order_by('date_time')
            show_filter = 'series_instances'
        except Event.DoesNotExist:
            events = Event.objects.none()
            parent_event = None
    elif show_filter == 'all':
        events = Event.objects.filter(archived=False).order_by('date_time')
        parent_event = None
    elif show_filter == 'past':
        events = Event.objects.filter(archived=False, date_time__lt=now).order_by('-date_time')
        parent_event = None
    elif show_filter == 'series':
        # Show only parent events (recurring series) with instance counts
        events = Event.objects.filter(archived=False, is_recurring=True, parent_event__isnull=True).order_by('date_time')
        parent_event = None
    elif show_filter == 'instances':
        # Show only recurring instances (child events)
        events = Event.objects.filter(archived=False, parent_event__isnull=False).order_by('date_time')
        parent_event = None
    else:  # upcoming (default)
        events = Event.objects.filter(archived=False, date_time__gte=now).order_by('date_time')
        parent_event = None

    # v3.17.4: manage_events.html renders `event.created_by.get_display_name`
    # twice per row and nothing joined it — one member fetch per event. Every
    # branch above builds its own queryset, so the join goes here where they
    # converge.
    #
    # v3.28.0: same N+1, different FK — `{% if event.parent_event %}` (plus
    # `.id`/`.title` in the "Instance" badge) fetches the parent Event row
    # once per event in the list. `parent_event` is self-referential
    # (Event -> Event), so no member_defer() needed here, just the join.
    events = events.select_related('created_by', 'parent_event').defer(*member_defer('created_by'))

    # For series view, add instance counts
    if show_filter == 'series':
        events = events.annotate(instance_count=Count('recurring_instances'))

    # Annotate signup counts for sign-up events
    events = events.annotate(
        signup_count=Count('signups', filter=Q(signups__is_cancelled=False))
    )

    # Pagination - 25 events per page
    paginator = Paginator(events, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'events': page_obj,  # Now a page object instead of queryset
        'page_obj': page_obj,
        'current_time': now,
        'current_filter': show_filter,
        'series_parent': parent_event if series_id else None,
        'series_id': series_id,
        'total_events': paginator.count,
    }
    return render(request, 'officer/manage_events.html', context)


def generate_recurring_events(parent_event, max_occurrences=52):
    """
    Generate recurring event instances based on the parent event's recurrence settings.
    Returns a list of Event objects (not saved to DB).
    """
    instances = []
    current_date = parent_event.date_time
    end_date = parent_event.recurrence_end_date

    # Default to 1 year from now if no end date
    if not end_date:
        end_date = (timezone.now() + timedelta(days=365)).date()

    recurrence_type = parent_event.recurrence_type
    interval = parent_event.recurrence_interval or 1
    unit = parent_event.recurrence_unit or 'weeks'
    selected_days = parent_event.recurrence_days or []

    count = 0
    while count < max_occurrences:
        # Calculate next occurrence based on recurrence type
        if recurrence_type == 'daily':
            current_date = current_date + timedelta(days=1)
        elif recurrence_type == 'weekly':
            current_date = current_date + timedelta(weeks=1)
        elif recurrence_type == 'biweekly':
            current_date = current_date + timedelta(weeks=2)
        elif recurrence_type == 'monthly':
            current_date = current_date + relativedelta(months=1)
        elif recurrence_type == 'custom':
            if unit == 'days':
                current_date = current_date + timedelta(days=interval)
            elif unit == 'weeks':
                current_date = current_date + timedelta(weeks=interval)
            elif unit == 'months':
                current_date = current_date + relativedelta(months=interval)
        else:
            break

        # Check if we've passed the end date
        if current_date.date() > end_date:
            break

        # For weekly/biweekly/custom-weeks, check if this day is selected
        if recurrence_type in ['weekly', 'biweekly'] or (recurrence_type == 'custom' and unit == 'weeks'):
            if selected_days and str(current_date.weekday()) not in [str(d) for d in selected_days]:
                # Skip this day - not in selected days
                # But we need to check other days in this week
                continue

        # Calculate shifted excuse deadline for this occurrence
        instance_excuse_deadline = None
        if parent_event.excuse_deadline:
            # Shift the excuse deadline by the same offset as the event date
            time_offset = current_date - parent_event.date_time
            instance_excuse_deadline = parent_event.excuse_deadline + time_offset

        # Create a new event instance
        #
        # v3.28.2 — reminder configuration (both push and email, both slots)
        # is now copied from the parent to each generated instance. Before
        # this, a recurring event's reminders only ever fired once, off the
        # parent row itself: `send_event_reminder_pushes` reads
        # `reminder_N_enabled`/`reminder_N_hours_before`/
        # `reminder_N_email_enabled` per Event row, and every one of those
        # was left at the model default (all False/unset) on a generated
        # instance, so an officer who configured reminders on a weekly
        # meeting got them for the first occurrence and silence after that —
        # with no error, and nothing on the create/edit form suggesting the
        # setting wouldn't carry forward. `reminder_N_sent_at` is
        # deliberately NOT copied — each instance is its own Event row with
        # its own send state, and copying a sent timestamp from the parent
        # would make a freshly generated instance look like it had already
        # sent a reminder that it never actually dispatched.
        instance = Event(
            title=parent_event.title,
            description=parent_event.description,
            date_time=current_date,
            location=parent_event.location,
            visible_to=parent_event.visible_to,
            is_active=parent_event.is_active,
            requires_attendance=parent_event.requires_attendance,
            allow_excuses=parent_event.allow_excuses,
            excuse_deadline=instance_excuse_deadline,
            created_by=parent_event.created_by,
            parent_event=parent_event,
            is_recurring=False,  # Child events are not recurring themselves
            reminder_1_enabled=parent_event.reminder_1_enabled,
            reminder_1_hours_before=parent_event.reminder_1_hours_before,
            reminder_1_email_enabled=parent_event.reminder_1_email_enabled,
            reminder_2_enabled=parent_event.reminder_2_enabled,
            reminder_2_hours_before=parent_event.reminder_2_hours_before,
            reminder_2_email_enabled=parent_event.reminder_2_email_enabled,
        )
        instances.append(instance)
        count += 1

    return instances


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

            # If this is a recurring event, generate instances
            if event.is_recurring and event.recurrence_type != 'none':
                recurring_instances = generate_recurring_events(event)
                for instance in recurring_instances:
                    instance.save()

            # Note: We don't create in-app notifications for events because
            # events are displayed on the calendar page which all members access.
            # This saves significant database space (~1 row per member per event).

            messages.success(request, f'Event "{event.title}" created successfully.')
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
            messages.success(request, f'Event "{event.title}" updated successfully.')
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
        title = event.title
        event.delete()
        messages.success(request, f'Event "{title}" deleted successfully.')
        return redirect('manage_events')

    return render(request, 'officer/delete_event.html', {'event': event})


# 1x1 transparent GIF — same constant/approach as
# manage_announcements.track_email_view.
_PIXEL_GIF = base64.b64decode(
    'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
)


def track_event_reminder_email_view(request, log_id, user_id):
    """
    Track when a reminder email is opened. Returns a 1x1 transparent pixel.

    No login required — this is loaded as an <img> src from the recipient's
    mail client, which has no Parliament session. Same shape as
    manage_announcements.track_email_view: silently no-op on a bad/stale
    id rather than 404ing (a broken pixel would just show as a missing
    image in the email; a 404 has no visible effect either, so there is
    nothing gained by distinguishing the two failure cases to the client,
    and every extra thing this view does is one more way to leak whether a
    given (log, user) pair exists to whoever is poking at the URL).
    """
    try:
        recipient = EventReminderRecipient.objects.get(
            reminder_log_id=log_id, user__user_id=user_id,
        )
        # Keep the FIRST open time — a mail client can refetch the pixel on
        # every subsequent look at the message.
        if recipient.viewed_at is None:
            recipient.viewed_at = timezone.now()
            recipient.save(update_fields=['viewed_at'])
    except EventReminderRecipient.DoesNotExist:
        pass

    return HttpResponse(_PIXEL_GIF, content_type='image/gif')
