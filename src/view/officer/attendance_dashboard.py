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
from src.feature_flag_decorators import require_feature_flag


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance')
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

    # v3.17.3 (second pass): was five COUNT round trips over the same filtered
    # queryset — a total plus one per status — each of them re-running the
    # `event__in=events` subquery. Conditional aggregation evaluates all five in
    # a single pass. Same pattern as the legislation status tabs (v3.17.2) and
    # the activity-log category counts.
    _status = attendance_records.aggregate(
        n_total=Count('pk'),
        n_present=Count('pk', filter=Q(status='present')),
        n_absent=Count('pk', filter=Q(status='absent')),
        n_excused=Count('pk', filter=Q(status='excused')),
        n_late=Count('pk', filter=Q(status='late')),
    )
    total_records = _status['n_total']
    present_count = _status['n_present']
    absent_count = _status['n_absent']
    excused_count = _status['n_excused']
    late_count = _status['n_late']

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

    # Individual event statistics (recent events).
    #
    # v3.17.3 (second pass): was two COUNTs per event over the 15 shown — 30
    # round trips. One GROUP BY covers all of them.
    recent_event_list = list(events[:15])
    _per_event = {
        row['event']: row
        for row in Attendance.objects
        .filter(event__in=recent_event_list, attendance_type='event')
        .values('event')
        .annotate(
            total=Count('pk'),
            present=Count('pk', filter=Q(status__in=['present', 'late'])),
        )
    }
    recent_events = []
    for event in recent_event_list:
        row = _per_event.get(event.pk, {})
        total = row.get('total', 0)
        present = row.get('present', 0)
        recent_events.append({
            'event': event,
            'attendance_rate': round((present / total) * 100, 1) if total else 0,
            'present_count': present,
            'total_count': total,
        })

    # Member statistics.
    #
    # v3.17.3 (second pass): THIS WAS THE BIG ONE. The loop ran five COUNT
    # queries per active member — a total plus one per status — each of them
    # re-running the `event__in=events` subquery. At chapter scale that is
    # ~500 queries on a single page load of the officer attendance dashboard,
    # growing linearly with the roster, and it is the page an officer opens to
    # look at the roster.
    #
    # One GROUP BY over (user, status) replaces all of it. Members with no
    # attendance rows simply do not appear in the aggregate and fall through to
    # the zero defaults below, exactly as the `if member_total > 0` branch did.
    active_members = list(
        ParliamentUser.objects.filter(member_status='Active').order_by('name')
    )
    _by_member = {}
    for row in (Attendance.objects
                .filter(user__in=active_members, event__in=events,
                        attendance_type='event')
                .values('user', 'status')
                .annotate(n=Count('pk'))):
        _by_member.setdefault(row['user'], {})[row['status']] = row['n']

    member_stats = []
    for member in active_members:
        counts = _by_member.get(member.pk, {})
        member_total = sum(counts.values())
        member_present = counts.get('present', 0)
        member_late = counts.get('late', 0)
        member_absent = counts.get('absent', 0)
        member_excused = counts.get('excused', 0)
        member_rate = (
            round(((member_present + member_late) / member_total) * 100, 1)
            if member_total else 0
        )

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
@require_feature_flag('attendance_tracking', 'event_attendance')
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
