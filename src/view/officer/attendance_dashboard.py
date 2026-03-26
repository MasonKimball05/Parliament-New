"""
Attendance Dashboard for Officers
Provides chapter-wide statistics, member breakdowns, and event series analysis
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Count, Q, Avg, F
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict

from src.models import Event, Attendance, ParliamentUser, AttendanceExcuse
from src.decorators import officer_required


@login_required
@officer_required
def attendance_dashboard(request):
    """
    Officer attendance dashboard with chapter-wide statistics
    """
    now = timezone.now()

    # Date range filter
    range_filter = request.GET.get('range', '90')
    try:
        days = int(range_filter)
    except ValueError:
        days = 90

    if days == 0:
        # All time
        start_date = None
    else:
        start_date = now - timedelta(days=days)

    # Get events that require attendance
    events_query = Event.objects.filter(
        requires_attendance=True,
        date_time__lt=now,  # Only past events
        is_active=True
    )

    if start_date:
        events_query = events_query.filter(date_time__gte=start_date)

    events = events_query.order_by('-date_time')

    # Calculate overall chapter statistics
    total_events = events.count()

    # Get all attendance records for these events
    attendance_records = Attendance.objects.filter(
        event__in=events,
        attendance_type='event'
    )

    total_records = attendance_records.count()
    present_count = attendance_records.filter(status='present').count()
    absent_count = attendance_records.filter(status='absent').count()
    excused_count = attendance_records.filter(status='excused').count()
    late_count = attendance_records.filter(status='late').count()

    # Calculate attendance rate (present + late + excused as "attended/excused")
    if total_records > 0:
        attendance_rate = round(((present_count + late_count) / total_records) * 100, 1)
        excused_rate = round((excused_count / total_records) * 100, 1)
    else:
        attendance_rate = 0
        excused_rate = 0

    chapter_stats = {
        'total_events': total_events,
        'total_records': total_records,
        'present': present_count,
        'absent': absent_count,
        'excused': excused_count,
        'late': late_count,
        'attendance_rate': attendance_rate,
        'excused_rate': excused_rate,
    }

    # Monthly attendance trends (for chart)
    monthly_trends = []
    if start_date:
        # Group by month
        monthly_data = attendance_records.annotate(
            month=TruncMonth('event__date_time')
        ).values('month').annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status='present')),
            late=Count('id', filter=Q(status='late')),
            absent=Count('id', filter=Q(status='absent')),
            excused=Count('id', filter=Q(status='excused')),
        ).order_by('month')

        for entry in monthly_data:
            if entry['month'] and entry['total'] > 0:
                rate = round(((entry['present'] + entry['late']) / entry['total']) * 100, 1)
                monthly_trends.append({
                    'month': entry['month'].strftime('%b %Y'),
                    'rate': rate,
                    'total': entry['total'],
                    'present': entry['present'],
                    'late': entry['late'],
                    'absent': entry['absent'],
                    'excused': entry['excused'],
                })

    # Event series statistics (recurring events grouped)
    event_series_stats = []

    # Get parent events that have recurring instances
    parent_events = events.filter(
        is_recurring=True,
        parent_event__isnull=True
    ).distinct()

    for parent in parent_events:
        # Get all instances including parent
        series_events = Event.objects.filter(
            Q(pk=parent.pk) | Q(parent_event=parent),
            date_time__lt=now
        )
        if start_date:
            series_events = series_events.filter(date_time__gte=start_date)

        series_attendance = Attendance.objects.filter(
            event__in=series_events,
            attendance_type='event'
        )

        series_total = series_attendance.count()
        if series_total > 0:
            series_present = series_attendance.filter(status__in=['present', 'late']).count()
            series_rate = round((series_present / series_total) * 100, 1)

            event_series_stats.append({
                'title': parent.title,
                'event_count': series_events.count(),
                'attendance_rate': series_rate,
                'total_records': series_total,
            })

    # Sort by attendance rate
    event_series_stats.sort(key=lambda x: x['attendance_rate'], reverse=True)

    # Individual event statistics (recent events)
    recent_events = []
    for event in events[:15]:  # Last 15 events
        event_attendance = Attendance.objects.filter(
            event=event,
            attendance_type='event'
        )
        total = event_attendance.count()
        if total > 0:
            present = event_attendance.filter(status__in=['present', 'late']).count()
            rate = round((present / total) * 100, 1)
        else:
            rate = 0
            present = 0

        recent_events.append({
            'event': event,
            'attendance_rate': rate,
            'present_count': present,
            'total_count': total,
        })

    # Member statistics
    active_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')
    member_stats = []

    for member in active_members:
        member_attendance = Attendance.objects.filter(
            user=member,
            event__in=events,
            attendance_type='event'
        )

        member_total = member_attendance.count()
        if member_total > 0:
            member_present = member_attendance.filter(status='present').count()
            member_late = member_attendance.filter(status='late').count()
            member_absent = member_attendance.filter(status='absent').count()
            member_excused = member_attendance.filter(status='excused').count()
            member_rate = round(((member_present + member_late) / member_total) * 100, 1)
        else:
            member_present = 0
            member_late = 0
            member_absent = 0
            member_excused = 0
            member_rate = 0

        member_stats.append({
            'member': member,
            'total': member_total,
            'present': member_present,
            'late': member_late,
            'absent': member_absent,
            'excused': member_excused,
            'attendance_rate': member_rate,
        })

    # Sort by attendance rate (descending), then by name
    member_stats.sort(key=lambda x: (-x['attendance_rate'], x['member'].name))

    # Pending excuses count
    pending_excuses = AttendanceExcuse.objects.filter(status='pending').count()

    context = {
        'chapter_stats': chapter_stats,
        'monthly_trends': monthly_trends,
        'event_series_stats': event_series_stats,
        'recent_events': recent_events,
        'member_stats': member_stats,
        'pending_excuses': pending_excuses,
        'range_filter': range_filter,
        'start_date': start_date,
    }

    return render(request, 'officer/attendance_dashboard.html', context)


@login_required
@officer_required
def member_attendance_detail(request, user_id):
    """
    Detailed attendance view for a specific member (officer view)
    """
    member = ParliamentUser.objects.get(user_id=user_id)
    now = timezone.now()

    # Date range filter
    range_filter = request.GET.get('range', '90')
    try:
        days = int(range_filter)
    except ValueError:
        days = 90

    if days == 0:
        start_date = None
    else:
        start_date = now - timedelta(days=days)

    # Get events that require attendance
    events_query = Event.objects.filter(
        requires_attendance=True,
        date_time__lt=now,
        is_active=True
    )

    if start_date:
        events_query = events_query.filter(date_time__gte=start_date)

    events = events_query.order_by('-date_time')

    # Member's attendance records
    member_attendance = Attendance.objects.filter(
        user=member,
        event__in=events,
        attendance_type='event'
    ).select_related('event')

    # Calculate stats
    total = member_attendance.count()
    if total > 0:
        present = member_attendance.filter(status='present').count()
        late = member_attendance.filter(status='late').count()
        absent = member_attendance.filter(status='absent').count()
        excused = member_attendance.filter(status='excused').count()
        attendance_rate = round(((present + late) / total) * 100, 1)
    else:
        present = late = absent = excused = 0
        attendance_rate = 0

    member_stats = {
        'total': total,
        'present': present,
        'late': late,
        'absent': absent,
        'excused': excused,
        'attendance_rate': attendance_rate,
    }

    # Get chapter average for comparison
    all_attendance = Attendance.objects.filter(
        event__in=events,
        attendance_type='event'
    )
    all_total = all_attendance.count()
    if all_total > 0:
        all_present = all_attendance.filter(status__in=['present', 'late']).count()
        chapter_average = round((all_present / all_total) * 100, 1)
    else:
        chapter_average = 0

    # Attendance history
    attendance_history = []
    for event in events:
        record = member_attendance.filter(event=event).first()
        excuse = AttendanceExcuse.objects.filter(
            event=event,
            user=member
        ).first()

        attendance_history.append({
            'event': event,
            'status': record.status if record else 'not_marked',
            'excuse': excuse,
        })

    context = {
        'member': member,
        'member_stats': member_stats,
        'chapter_average': chapter_average,
        'attendance_history': attendance_history,
        'range_filter': range_filter,
    }

    return render(request, 'officer/member_attendance_detail.html', context)
