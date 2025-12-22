from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.http import JsonResponse
from src.models import Event
import calendar
from datetime import datetime, timedelta
from collections import defaultdict

@login_required
def calendar_view(request):
    """Display calendar with events marked on specific days"""
    now = timezone.now()

    # Get month and year from query params, default to current month
    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    # Create calendar for the specified month
    cal = calendar.monthcalendar(year, month)
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

    # Get all active events for this month
    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    events = Event.objects.filter(
        is_active=True,
        date_time__gte=month_start,
        date_time__lt=month_end
    ).order_by('date_time')

    # Group events by day
    events_by_day = defaultdict(list)
    for event in events:
        day = event.date_time.day
        events_by_day[day].append(event)

    # Get upcoming events (next 5 from today)
    upcoming_events = Event.objects.filter(
        is_active=True,
        date_time__gte=now
    ).order_by('date_time')[:5]

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
        'today': now.day if now.year == year and now.month == month else None,
    }

    return render(request, 'calendar.html', context)


@login_required
def calendar_data_api(request):
    """API endpoint for fetching calendar data via AJAX"""
    now = timezone.now()

    year = int(request.GET.get('year', now.year))
    month = int(request.GET.get('month', now.month))

    # Create calendar for the specified month
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]

    # Get all active events for this month
    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    events = Event.objects.filter(
        is_active=True,
        date_time__gte=month_start,
        date_time__lt=month_end
    ).order_by('date_time')

    # Build events data
    events_data = {}
    for event in events:
        day = event.date_time.day
        if day not in events_data:
            events_data[day] = []
        events_data[day].append({
            'id': event.id,
            'title': event.title,
            'description': event.description,
            'time': event.date_time.strftime('%I:%M %p'),
            'full_datetime': event.date_time.strftime('%A, %B %d, %Y at %I:%M %p'),
            'location': event.location or '',
            'created_by': event.created_by.get_display_name(),
        })

    data = {
        'calendar': cal,
        'month_name': month_name,
        'year': year,
        'month': month,
        'events': events_data,
        'today': now.day if now.year == year and now.month == month else None,
    }

    return JsonResponse(data)
