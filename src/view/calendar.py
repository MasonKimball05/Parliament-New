from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from src.models import Event, ActivityLog, AttendanceExcuse
from src.models_calendar_subscription import CalendarSubscription
from src.feature_flag_decorators import require_page_enabled
from icalendar import Calendar, Event as ICalEvent
import calendar
from datetime import datetime, timedelta
from collections import defaultdict
import pytz

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
    ).order_by('date_time')

    # Filter by visibility
    events = [e for e in all_events if e.is_visible_to_user(request.user)]

    # Group events by day (use localtime to get the correct day)
    events_by_day = defaultdict(list)
    for event in events:
        # Convert to local timezone before extracting day
        local_dt = timezone.localtime(event.date_time)
        day = local_dt.day
        events_by_day[day].append(event)

    # Get upcoming events (next 5 from today)
    all_upcoming = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now
    ).order_by('date_time')
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
    ).order_by('date_time')

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
        has_excuse = AttendanceExcuse.objects.filter(event=event, user=request.user).exists()
        excuse_status = None
        if has_excuse:
            excuse = AttendanceExcuse.objects.filter(event=event, user=request.user).first()
            excuse_status = excuse.status

        # Format time without leading zeros (use local timezone)
        hour = local_dt.strftime('%I').lstrip('0')
        minute = local_dt.strftime('%M')
        am_pm = local_dt.strftime('%p')
        time_str = f"{hour}:{minute} {am_pm}"

        day_num = local_dt.strftime('%d').lstrip('0')
        year = local_dt.strftime('%Y')
        full_datetime_str = f"{local_dt.strftime('%A, %B')} {day_num}, {year} at {time_str}"

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

    # Get all active upcoming events
    all_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now,
        date_time__lte=end_date
    ).order_by('date_time')

    # Filter by visibility
    events = [e for e in all_events if e.is_visible_to_user(request.user)]

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

    # Get all active events in range
    all_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=start_date,
        date_time__lte=end_date
    ).order_by('date_time')

    # Filter by visibility - only show events this user can see
    events = [e for e in all_events if e.is_visible_to_user(user)]

    # Create iCal calendar
    cal = Calendar()
    cal.add('prodid', '-//Parliament Chapter Calendar//am-parliament.org//')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', f'Chapter Events - {user.get_display_name()}')
    cal.add('x-wr-caldesc', 'Personal chapter events calendar - automatically updated')
    cal.add('x-wr-timezone', 'America/Chicago')
    cal.add('refresh-interval', 'PT1H')  # Suggest 1 hour refresh
    cal.add('x-published-ttl', 'PT1H')

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
