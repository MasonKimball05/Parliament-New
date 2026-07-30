from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.db import transaction, models
from src.models import Event, EventSignup, ActivityLog, AttendanceExcuse
from src.models_calendar_subscription import CalendarSubscription
from src.feature_flag_decorators import require_page_enabled, require_feature_flag
from icalendar import Calendar, Event as ICalEvent
from icalendar.prop import vDuration
import calendar
from datetime import datetime, timedelta
from collections import defaultdict
import pytz
from src.models.users import member_defer
from src.utils.visibility import visible_to_q
from src.decorators import officer_required

@login_required
@require_page_enabled('calendar')
def calendar_view(request):
    """Display calendar with events marked on specific days"""
    now = timezone.now()

    # Get month and year from query params, default to current month
    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    # Calculate date range limits (1 year back and 1 year forward)
    min_date = now - timedelta(days=365)
    max_date = now + timedelta(days=365)

    # Clamp requested date to valid range
    requested_date = datetime(year, month, 1)
    if requested_date < datetime(min_date.year, min_date.month, 1):
        year, month = min_date.year, min_date.month
    elif requested_date > datetime(max_date.year, max_date.month, 1):
        year, month = max_date.year, max_date.month

    # Create calendar for the specified month - set first day to Sunday (6)
    cal_obj = calendar.Calendar(firstweekday=6)  # 6 = Sunday
    cal = cal_obj.monthdayscalendar(year, month)
    month_name = calendar.month_name[month]

    # Calculate previous and next month
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    # Check if prev/next navigation should be disabled
    # Use timezone-aware datetimes
    tz = pytz.timezone('UTC')
    prev_date = tz.localize(datetime(prev_year, prev_month, 1))
    next_date = tz.localize(datetime(next_year, next_month, 1))
    can_go_prev = prev_date >= tz.localize(datetime(min_date.year, min_date.month, 1))
    can_go_next = next_date <= tz.localize(datetime(max_date.year, max_date.month, 1))

    # Get all active events for this month
    month_start = tz.localize(datetime(year, month, 1))
    if month == 12:
        month_end = tz.localize(datetime(year + 1, 1, 1))
    else:
        month_end = tz.localize(datetime(year, month + 1, 1))

    all_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=month_start,
        date_time__lt=month_end
    ).select_related('service_event', 'recruitment_event').order_by('date_time')

    # Filter by visibility
    events = [e for e in all_events if e.is_visible_to_user(request.user)]

    # Group events by day (use localtime to get the correct day)
    events_by_day = defaultdict(list)
    for event in events:
        # Convert to local timezone before extracting day
        local_dt = timezone.localtime(event.date_time)
        day = local_dt.day
        events_by_day[day].append(event)

    # Prefetch signup state for this month's events (two queries, no N+1)
    signup_event_ids = [e.id for e in events if e.requires_signup]
    user_signed_up_ids = set()
    signup_counts = {}
    if signup_event_ids:
        from django.db.models import Count
        user_signed_up_ids = set(
            EventSignup.objects
            .filter(user=request.user, event_id__in=signup_event_ids, is_cancelled=False)
            .values_list('event_id', flat=True)
        )
        signup_counts = dict(
            EventSignup.objects
            .filter(event_id__in=signup_event_ids, is_cancelled=False)
            .values('event_id')
            .annotate(c=Count('id'))
            .values_list('event_id', 'c')
        )

    # Get upcoming events (next 5 from today)
    all_upcoming = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now
    ).select_related('service_event', 'recruitment_event').order_by('date_time')
    upcoming_events = [e for e in all_upcoming if e.is_visible_to_user(request.user)][:5]

    # Get local time for today's date
    local_now = timezone.localtime(now)

    context = {
        'calendar': cal,
        'month': month,
        'month_name': month_name,
        'year': year,
        'events_by_day': dict(events_by_day),
        'upcoming_events': upcoming_events,
        'current_time': now,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'today': local_now.day if local_now.year == year and local_now.month == month else None,
        'can_go_prev': can_go_prev,
        'can_go_next': can_go_next,
        'user_signed_up_ids': user_signed_up_ids,
        'signup_counts': signup_counts,
    }

    return render(request, 'calendar.html', context)


@login_required
def calendar_data_api(request):
    """API endpoint for fetching calendar data via AJAX (visible to all members)"""
    now = timezone.now()

    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    # Calculate date range limits (1 year back and 1 year forward)
    min_date = now - timedelta(days=365)
    max_date = now + timedelta(days=365)

    # Clamp requested date to valid range
    requested_date = datetime(year, month, 1)
    if requested_date < datetime(min_date.year, min_date.month, 1):
        year, month = min_date.year, min_date.month
    elif requested_date > datetime(max_date.year, max_date.month, 1):
        year, month = max_date.year, max_date.month

    # Create calendar for the specified month - set first day to Sunday (6)
    cal_obj = calendar.Calendar(firstweekday=6)  # 6 = Sunday
    cal = cal_obj.monthdayscalendar(year, month)
    month_name = calendar.month_name[month]

    # Calculate previous and next month
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    # Check if prev/next navigation should be disabled
    # Use timezone-aware datetimes
    tz = pytz.timezone('UTC')
    prev_date = tz.localize(datetime(prev_year, prev_month, 1))
    next_date = tz.localize(datetime(next_year, next_month, 1))
    can_go_prev = prev_date >= tz.localize(datetime(min_date.year, min_date.month, 1))
    can_go_next = next_date <= tz.localize(datetime(max_date.year, max_date.month, 1))

    # Get all active events for this month
    month_start = tz.localize(datetime(year, month, 1))
    if month == 12:
        month_end = tz.localize(datetime(year + 1, 1, 1))
    else:
        month_end = tz.localize(datetime(year, month + 1, 1))

    all_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=month_start,
        date_time__lt=month_end
    ).select_related('service_event', 'recruitment_event').order_by('date_time')

    # Filter by visibility
    events = [e for e in all_events if e.is_visible_to_user(request.user)]

    # Build events data
    events_data = {}
    for event in events:
        # Convert to local timezone before extracting day and formatting
        local_dt = timezone.localtime(event.date_time)
        day = local_dt.day
        if day not in events_data:
            events_data[day] = []

        # Check excuse status for this user
        excuse = AttendanceExcuse.objects.filter(event=event, user=request.user).first()
        has_excuse = excuse is not None
        excuse_status = excuse.status if excuse else None

        # Format time without leading zeros (use local timezone)
        hour = local_dt.strftime('%I').lstrip('0')
        minute = local_dt.strftime('%M')
        am_pm = local_dt.strftime('%p')
        time_str = f"{hour}:{minute} {am_pm}"

        day_num = local_dt.strftime('%d').lstrip('0')
        year_str = local_dt.strftime('%Y')
        full_datetime_str = f"{local_dt.strftime('%A, %B')} {day_num}, {year_str} at {time_str}"

        try:
            is_service_event = event.service_event is not None
        except Exception:
            is_service_event = False

        try:
            is_recruitment_event = event.recruitment_event is not None
        except Exception:
            is_recruitment_event = False

        # Signup state
        signup_count = 0
        user_signed_up = False
        if event.requires_signup:
            signup_count = event.signups.filter(is_cancelled=False).count()
            user_signed_up = event.signups.filter(user=request.user, is_cancelled=False).exists()

        events_data[day].append({
            'id': event.id,
            'title': event.title,
            'description': event.description,
            'time': time_str,
            'full_datetime': full_datetime_str,
            'location': event.location or '',
            'created_by': event.created_by.get_display_name(),
            'requires_attendance': event.requires_attendance,
            'allow_excuses': event.allow_excuses,
            'can_submit_excuse': event.can_submit_excuse() if event.allow_excuses else False,
            'has_excuse': has_excuse,
            'excuse_status': excuse_status,
            'is_service_event': is_service_event,
            'is_recruitment_event': is_recruitment_event,
            'requires_signup': event.requires_signup,
            'max_signups': event.max_signups,
            'signups_open': event.signups_open,
            'signup_count': signup_count,
            'user_signed_up': user_signed_up,
            'signup_full': event.max_signups is not None and signup_count >= event.max_signups,
            # v3.15.0 QOL: add-to-calendar links for the event modal
            'google_url': event.google_calendar_url,
            'ics_url': reverse('export_event_ical', args=[event.id]),
        })

    # Get local time for today's date
    local_now = timezone.localtime(now)

    data = {
        'calendar': cal,
        'month_name': month_name,
        'year': year,
        'month': month,
        'events': events_data,
        'today': local_now.day if local_now.year == year and local_now.month == month else None,
        'can_go_prev': can_go_prev,
        'can_go_next': can_go_next,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
    }

    response = JsonResponse(data)
    # Add cache-busting headers to prevent browser caching
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response


@login_required
def export_calendar_ical(request):
    """
    Export upcoming events to iCal format (.ics file)
    Users can import this into Google Calendar, Apple Calendar, Outlook, etc.
    """
    now = timezone.now()

    # Get time range from query params (default: next 90 days)
    days_ahead = int(request.GET.get('days', 90))
    end_date = now + timedelta(days=days_ahead)

    # Get all active upcoming events.
    #
    # v3.17.3 (second pass): the loop below prints `event.created_by.get_display_name()`,
    # and this queryset had no select_related — so exporting a 90-day calendar
    # fired one full ParliamentUser fetch per event. Visibility is also filtered
    # in SQL now rather than by walking every event in the window in Python;
    # `visible_to_q` is the same rule `is_visible_to_user` applies, with a test
    # asserting they agree.
    events = list(
        Event.objects.filter(
            is_active=True,
            archived=False,
            date_time__gte=now,
            date_time__lte=end_date,
        )
        .filter(visible_to_q(request.user.member_type))
        .select_related('created_by')
        .defer(*member_defer('created_by'))
        .order_by('date_time')
    )

    # Create iCal calendar
    cal = Calendar()
    cal.add('prodid', '-//Parliament Chapter Calendar//am-parliament.org//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'Chapter Events')
    cal.add('x-wr-caldesc', 'Upcoming chapter events and meetings')

    # Add each event to the calendar
    for event in events:
        ical_event = ICalEvent()
        ical_event.add('summary', event.title)
        ical_event.add('dtstart', event.date_time)

        # Calculate end time (default 1 hour if not specified)
        end_time = event.date_time + timedelta(hours=1)
        ical_event.add('dtend', end_time)

        # Add description and location
        description = event.description or ''
        description += f'\n\nCreated by: {event.created_by.get_display_name()}'
        ical_event.add('description', description)

        if event.location:
            ical_event.add('location', event.location)

        # Add unique identifier
        ical_event.add('uid', f'event-{event.id}@am-parliament.org')
        ical_event.add('dtstamp', timezone.now())

        cal.add_component(ical_event)

    # Log the export
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported {len(events)} events to iCal',
        request=request,
        metadata={'event_count': len(events), 'days_ahead': days_ahead}
    )

    # Create response with iCal file
    response = HttpResponse(cal.to_ical(), content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="chapter_events_{now.strftime("%Y%m%d")}.ics"'
    return response


@login_required
def export_event_ical(request, event_id):
    """
    Export a single event to iCal format
    """
    event = get_object_or_404(Event, id=event_id, is_active=True, archived=False)

    # Check visibility
    if not event.is_visible_to_user(request.user):
        return HttpResponse('Event not found or access denied.', status=404)

    # Create iCal calendar
    cal = Calendar()
    cal.add('prodid', '-//Parliament Chapter Calendar//am-parliament.org//')
    cal.add('version', '2.0')

    # Create the event
    ical_event = ICalEvent()
    ical_event.add('summary', event.title)
    ical_event.add('dtstart', event.date_time)

    # Calculate end time (default 1 hour if not specified)
    end_time = event.date_time + timedelta(hours=1)
    ical_event.add('dtend', end_time)

    # Add description and location
    description = event.description or ''
    description += f'\n\nCreated by: {event.created_by.get_display_name()}'
    ical_event.add('description', description)

    if event.location:
        ical_event.add('location', event.location)

    # Add unique identifier
    ical_event.add('uid', f'event-{event.id}@am-parliament.org')
    ical_event.add('dtstamp', timezone.now())

    cal.add_component(ical_event)

    # Log the export
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported event "{event.title}" to iCal',
        request=request,
        object_type='Event',
        object_id=event.id,
        object_repr=event.title
    )

    # Create response with iCal file
    response = HttpResponse(cal.to_ical(), content_type='text/calendar; charset=utf-8')
    safe_filename = ''.join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in event.title)
    response['Content-Disposition'] = f'attachment; filename="{safe_filename}.ics"'
    return response


def calendar_subscription_feed(request, token):
    """
    Dynamic calendar subscription feed (webcal://)
    This URL can be subscribed to in calendar apps and will auto-update.
    No authentication required - uses secure token instead.

    Features:
    - Shows only events visible to the user
    - Auto-updates when calendar apps poll the feed
    - Includes past 30 days and future 365 days of events
    - Updates automatically when events are added/removed
    """
    # Validate token and get subscription
    try:
        subscription = CalendarSubscription.objects.get(token=token, is_active=True)
    except CalendarSubscription.DoesNotExist:
        return HttpResponse('Invalid or expired calendar subscription link.', status=404)

    # Record access
    subscription.record_access()

    user = subscription.user
    now = timezone.now()

    # Get events from past 30 days to future 365 days
    start_date = now - timedelta(days=30)
    end_date = now + timedelta(days=365)

    # Get all active events in range.
    #
    # v3.17.3 (second pass): same fix as export_calendar_ical — the loop prints
    # `event.created_by.get_display_name()`, and this feed covers a 13-month
    # window, so one member fetch per event was the largest instance of the
    # pattern on the site. This endpoint is polled by calendar clients on a
    # schedule, unattended, which makes it the worst place to leave an N+1.
    events = list(
        Event.objects.filter(
            is_active=True,
            archived=False,
            date_time__gte=start_date,
            date_time__lte=end_date,
        )
        .filter(visible_to_q(user.member_type))
        .select_related('created_by')
        .defer(*member_defer('created_by'))
        .order_by('date_time')
    )

    # Create iCal calendar
    cal = Calendar()
    cal.add('prodid', '-//Parliament Chapter Calendar//am-parliament.org//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', f'Chapter Events - {user.get_display_name()}')
    cal.add('x-wr-caldesc', 'Personal chapter events calendar - automatically updated')
    cal.add('x-wr-timezone', 'America/Chicago')
    # Suggest a 1-hour refresh to subscribing clients.
    #
    # v3.17.3: these were raw 'PT1H' strings. That produces correct output on
    # the pinned icalendar 6.1.0, but 6.x is the last version that accepts it:
    # on icalendar 7.x, Component.add() routes duration-valued properties
    # through the vDuration constructor, which rejects a str with
    # "You must use datetime, date, timedelta, time or tuple" — a TypeError
    # raised inside the view, i.e. a 500 on the calendar subscription feed.
    #
    # Passing an explicit vDuration is correct on both: it emits `PT1H` on
    # 6.1.0 (a bare timedelta does NOT — it renders as `1:00:00`, which is not
    # a valid ICS duration) and it is the type 7.x builds internally. Worth
    # doing now because there are open dependabot PRs, and this feature has
    # already been silently broken once before (the 07-25-26 flag-seeding bug).
    cal.add('refresh-interval', vDuration(timedelta(hours=1)))
    cal.add('x-published-ttl', vDuration(timedelta(hours=1)))

    # Add each event to the calendar
    for event in events:
        ical_event = ICalEvent()
        ical_event.add('summary', event.title)
        ical_event.add('dtstart', event.date_time)

        # Calculate end time (default 1 hour if not specified)
        end_time = event.date_time + timedelta(hours=1)
        ical_event.add('dtend', end_time)

        # Add description and location
        description = event.description or ''
        description += f'\n\nCreated by: {event.created_by.get_display_name()}'

        if event.requires_attendance:
            description += '\n\n⚠️ Attendance Required'

        ical_event.add('description', description)

        if event.location:
            ical_event.add('location', event.location)

        # Add unique identifier - IMPORTANT: Must be consistent for updates
        ical_event.add('uid', f'event-{event.id}@am-parliament.org')

        # Use event's updated timestamp if available, otherwise creation time
        ical_event.add('dtstamp', event.created_at)
        ical_event.add('last-modified', event.created_at)

        # Add sequence number for updates (calendar apps use this to detect changes)
        ical_event.add('sequence', 0)

        cal.add_component(ical_event)

    # Create response with proper headers for subscription
    response = HttpResponse(cal.to_ical(), content_type='text/calendar; charset=utf-8')

    # IMPORTANT: Don't set Content-Disposition to attachment - that forces download
    # For subscriptions, we want inline display
    response['Content-Disposition'] = f'inline; filename="chapter_calendar.ics"'

    # Add cache control headers - allow caching for 1 hour
    response['Cache-Control'] = 'public, max-age=3600'
    response['Expires'] = (now + timedelta(hours=1)).strftime('%a, %d %b %Y %H:%M:%S GMT')

    return response


@login_required
@require_feature_flag('calendar_subscriptions')
def get_calendar_subscription_url(request):
    """
    Get or create the user's personal calendar subscription URL
    Returns JSON with subscription URLs
    """
    # Get or create subscription for user
    subscription = CalendarSubscription.get_or_create_for_user(request.user)

    # Build full URL
    from django.urls import reverse
    feed_path = reverse('calendar_subscription_feed', kwargs={'token': subscription.token})

    # Get the request host
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()

    # Build URLs
    http_url = f'{scheme}://{host}{feed_path}'
    webcal_url = f'webcal://{host}{feed_path}'

    return JsonResponse({
        'http_url': http_url,
        'webcal_url': webcal_url,
        'token': subscription.token,
        'created_at': subscription.created_at.isoformat(),
        'last_accessed': subscription.last_accessed.isoformat() if subscription.last_accessed else None,
        'access_count': subscription.access_count,
    })


@login_required
@require_feature_flag('calendar_subscriptions')
def regenerate_calendar_token(request):
    """
    Regenerate the user's calendar subscription token
    Use this if the token is compromised
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    subscription = CalendarSubscription.get_or_create_for_user(request.user)
    new_token = subscription.regenerate_token()

    # Log the regeneration
    ActivityLog.log_activity(
        action_type='security',
        user=request.user,
        description=f'{request.user.get_display_name()} regenerated their calendar subscription token',
        request=request
    )

    # Build new URLs
    from django.urls import reverse
    feed_path = reverse('calendar_subscription_feed', kwargs={'token': new_token})
    scheme = 'https' if request.is_secure() else 'http'
    host = request.get_host()

    return JsonResponse({
        'success': True,
        'message': 'Calendar subscription token regenerated',
        'http_url': f'{scheme}://{host}{feed_path}',
        'webcal_url': f'webcal://{host}{feed_path}',
        'token': new_token,
    })


# ---------------------------------------------------------------------------
# Sign-up views
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('calendar')
def event_signup(request, event_id):
    """Sign the current user up for an event (POST only)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    # Lock the Event row so concurrent signups serialize through here.
    with transaction.atomic():
        event = get_object_or_404(
            Event.objects.select_for_update(),
            pk=event_id, is_active=True, requires_signup=True,
        )

        if not event.signups_open:
            return JsonResponse({'success': False, 'error': 'Sign-ups are closed for this event.'}, status=400)

        # Re-activate a cancelled signup or create fresh
        signup, created = EventSignup.objects.get_or_create(
            event=event,
            user=request.user,
            defaults={'is_cancelled': False, 'waitlist_position': None},
        )
        if not created:
            if not signup.is_cancelled:
                already_on = 'waitlist' if signup.waitlist_position is not None else 'sign-up list'
                return JsonResponse({'success': False, 'error': f'You are already on the {already_on}.'}, status=400)
            # Re-activating — reset cancellation state
            signup.is_cancelled = False
            signup.cancelled_at = None

        # Determine whether a confirmed slot is available
        confirmed_count = event.signups.filter(is_cancelled=False, waitlist_position__isnull=True).count()
        slot_available = event.max_signups is None or confirmed_count < event.max_signups

        if slot_available:
            signup.waitlist_position = None
            signup.save(update_fields=['is_cancelled', 'cancelled_at', 'waitlist_position'])
            on_waitlist = False
        elif event.allow_waitlist:
            # Place on waitlist at the next available position
            next_pos = (
                event.signups
                .filter(is_cancelled=False, waitlist_position__isnull=False)
                .order_by('-waitlist_position')
                .values_list('waitlist_position', flat=True)
                .first() or 0
            ) + 1
            signup.waitlist_position = next_pos
            signup.save(update_fields=['is_cancelled', 'cancelled_at', 'waitlist_position'])
            on_waitlist = True
        else:
            if created:
                signup.delete()
            return JsonResponse({'success': False, 'error': 'This event is full.'}, status=400)

        confirmed_count = event.signups.filter(is_cancelled=False, waitlist_position__isnull=True).count()
        waitlist_count = event.signups.filter(is_cancelled=False, waitlist_position__isnull=False).count()

    return JsonResponse({
        'success': True,
        'signup_count': confirmed_count,
        'waitlist_count': waitlist_count,
        'user_signed_up': True,
        'on_waitlist': on_waitlist,
        'waitlist_position': signup.waitlist_position,
        'signup_full': event.max_signups is not None and confirmed_count >= event.max_signups,
    })


@login_required
@require_page_enabled('calendar')
def event_cancel_signup(request, event_id):
    """Cancel the current user's signup for an event (POST only)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    with transaction.atomic():
        event = get_object_or_404(
            Event.objects.select_for_update(),
            pk=event_id, is_active=True, requires_signup=True,
        )
        signup = EventSignup.objects.filter(event=event, user=request.user, is_cancelled=False).first()
        if not signup:
            return JsonResponse({'success': False, 'error': 'You are not signed up for this event.'}, status=400)

        was_confirmed = signup.waitlist_position is None
        signup.is_cancelled = True
        signup.cancelled_at = timezone.now()
        signup.save(update_fields=['is_cancelled', 'cancelled_at'])

        # If they held a confirmed slot, promote the first waitlisted person
        if was_confirmed and event.allow_waitlist:
            first_waiting = (
                EventSignup.objects
                .filter(event=event, is_cancelled=False, waitlist_position__isnull=False)
                .order_by('waitlist_position')
                .first()
            )
            if first_waiting:
                first_waiting.waitlist_position = None
                first_waiting.save(update_fields=['waitlist_position'])
                # Compact remaining waitlist positions
                EventSignup.objects.filter(
                    event=event, is_cancelled=False, waitlist_position__isnull=False,
                ).order_by('waitlist_position').update(
                    waitlist_position=models.F('waitlist_position') - 1
                )

        confirmed_count = event.signups.filter(is_cancelled=False, waitlist_position__isnull=True).count()
        waitlist_count = event.signups.filter(is_cancelled=False, waitlist_position__isnull=False).count()

    return JsonResponse({
        'success': True,
        'signup_count': confirmed_count,
        'waitlist_count': waitlist_count,
        'user_signed_up': False,
        'signup_full': event.max_signups is not None and confirmed_count >= event.max_signups,
    })


@login_required
@officer_required
@require_page_enabled('calendar')
def event_signup_list(request, event_id):
    """Officer view — list all active sign-ups for an event, including waitlist."""
    # v3.17.3: was `from src.utils.officer_check import is_officer` — a module
    # that has NEVER existed. The import was added in v3.9.1 and the module was
    # never created, so this view has been a hard 500 (ModuleNotFoundError) ever
    # since.
    #
    # v3.17.5: that fix reached for `request.user.is_officer`, which is a real
    # property but NOT what the rest of the app means by "officer" — it is
    # `member_type == 'Officer' or is_admin`, so it **excluded Chairs**. Every
    # other officer view uses @officer_required, which admits officers, chairs
    # and admins; a Chair would have got a 403 on the signup list for an event
    # they run. Nobody ever hit it because the view 500'd before reaching the
    # check, so this would have shipped as a first-appearance bug.
    #
    # Using the decorator also puts the denial through `_gate()`, so it is
    # visible in dev mode's Perms tab and in the authz log, and returns the
    # app's standard 403 body instead of a bare PermissionDenied.
    event = get_object_or_404(Event, pk=event_id, is_active=True, requires_signup=True)

    all_active = list(
        EventSignup.objects
        .filter(event=event, is_cancelled=False)
        .select_related('user').defer(*member_defer('user'))
        .order_by('waitlist_position', 'signed_up_at')
    )
    signups   = [s for s in all_active if s.waitlist_position is None]
    waitlist  = [s for s in all_active if s.waitlist_position is not None]
    cancelled = list(
        EventSignup.objects
        .filter(event=event, is_cancelled=True)
        .select_related('user').defer(*member_defer('user'))
        .order_by('cancelled_at')
    )

    return render(request, 'calendar/event_signup_list.html', {
        'event': event,
        'signups': signups,
        'waitlist': waitlist,
        'cancelled': cancelled,
        'signup_count': len(signups),
        'waitlist_count': len(waitlist),
    })


@login_required
@officer_required
@require_page_enabled('calendar')
def event_signup_export(request, event_id):
    """
    Officer-only CSV download of the active sign-up (and waitlist) roster.

    ⚠️ This writes Name and Email for every member signed up to the event, so
    it is a bulk member-data export in the same class as the directory and
    user-list exports. It is therefore listed in
    `GeoRestrictionMiddleware.RESTRICTED_EXPORT_VIEWS` (v3.17.5) — that list is
    keyed on URL name precisely because this route has a parameter in the
    middle of it and could not be expressed as a path prefix.
    """
    import csv

    # v3.17.3 revived this view (see event_signup_list for the history);
    # v3.17.5 moved the gate onto @officer_required so Chairs are admitted and
    # the denial is logged. Do not put the inline `is_officer` check back.
    event = get_object_or_404(Event, pk=event_id, is_active=True, requires_signup=True)

    all_active = (
        EventSignup.objects
        .filter(event=event, is_cancelled=False)
        .select_related('user').defer(*member_defer('user'))
        .order_by('waitlist_position', 'signed_up_at')
    )

    filename = (
        f"signups_{event.title.replace(' ', '_')}"
        f"_{event.date_time.strftime('%Y%m%d')}.csv"
    )
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'

    writer = csv.writer(response)
    writer.writerow(['Status', 'Waitlist Position', 'Name', 'Email', 'Signed Up At'])
    for s in all_active:
        if s.waitlist_position is not None:
            status = f'Waitlist #{s.waitlist_position}'
        else:
            status = 'Confirmed'
        writer.writerow([
            status,
            s.waitlist_position or '',
            s.user.name,
            s.user.email,
            s.signed_up_at.strftime('%Y-%m-%d %H:%M'),
        ])

    return response
