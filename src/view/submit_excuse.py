"""
Member-facing views for attendance and excuse requests
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
from datetime import timedelta

from src.models import Event, Attendance, AttendanceExcuse, ActivityLog, ParliamentUser
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_feature_flag
from src.models.users import member_defer


@login_required
@require_feature_flag('attendance_tracking')
def my_excuses(request):
    """
    View member's excuse requests and submit new ones
    """
    # Get member's existing excuse requests
    my_excuse_requests = AttendanceExcuse.objects.filter(
        user=request.user
    ).select_related('event', 'reviewed_by').defer(*member_defer('reviewed_by')).order_by('-submitted_at')

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

    # Calculate status counts for summary cards
    excuse_counts = {
        'pending': my_excuse_requests.filter(status='pending').count(),
        'approved': my_excuse_requests.filter(status='approved').count(),
        'denied': my_excuse_requests.filter(status='denied').count(),
        'total': my_excuse_requests.count(),
    }

    context = {
        'my_excuses': my_excuse_requests,
        'available_events': available_events,
        'excuse_counts': excuse_counts,
    }

    return render(request, 'my_excuses.html', context)


@login_required
@require_feature_flag('attendance_tracking')
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

    # Check if excuses are still accepted
    if not event.can_submit_excuse():
        if event.attendance_finalized:
            messages.error(request, 'Attendance for this event has been finalized. Excuses can no longer be submitted.')
        else:
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

        # Validate uploaded file if provided
        if supporting_document:
            try:
                validate_uploaded_file(supporting_document)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'submit_excuse.html', {'event': event})

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
@require_feature_flag('attendance_tracking')
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


@login_required
@require_feature_flag('attendance_tracking')
def my_attendance(request):
    """
    Personal attendance dashboard for members
    Shows attendance history, stats, and comparison to chapter average
    """
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

    # Filter to events visible to this user
    all_events = events_query.order_by('-date_time')
    events = [e for e in all_events if e.is_visible_to_user(request.user)]

    # Get user's attendance records
    my_attendance_records = Attendance.objects.filter(
        user=request.user,
        event__in=events,
        attendance_type='event'
    ).select_related('event')

    # Calculate personal stats
    total = my_attendance_records.count()
    if total > 0:
        present = my_attendance_records.filter(status='present').count()
        late = my_attendance_records.filter(status='late').count()
        absent = my_attendance_records.filter(status='absent').count()
        excused = my_attendance_records.filter(status='excused').count()
        attendance_rate = round(((present + late) / total) * 100, 1)
    else:
        present = late = absent = excused = 0
        attendance_rate = 0

    my_stats = {
        'total': total,
        'present': present,
        'late': late,
        'absent': absent,
        'excused': excused,
        'attendance_rate': attendance_rate,
    }

    # Get chapter average for comparison
    chapter_attendance = Attendance.objects.filter(
        event__in=events,
        attendance_type='event'
    )
    chapter_total = chapter_attendance.count()
    if chapter_total > 0:
        chapter_present = chapter_attendance.filter(status__in=['present', 'late']).count()
        chapter_average = round((chapter_present / chapter_total) * 100, 1)
    else:
        chapter_average = 0

    # Calculate difference from chapter average
    if my_stats['total'] > 0 and chapter_total > 0:
        rate_difference = round(my_stats['attendance_rate'] - chapter_average, 1)
    else:
        rate_difference = 0

    # Build attendance history
    attendance_history = []
    for event in events:
        record = my_attendance_records.filter(event=event).first()
        excuse = AttendanceExcuse.objects.filter(
            event=event,
            user=request.user
        ).first()

        attendance_history.append({
            'event': event,
            'status': record.status if record else 'not_marked',
            'excuse': excuse,
        })

    # Get stats by event series (recurring events)
    series_stats = []
    parent_events = Event.objects.filter(
        is_recurring=True,
        parent_event__isnull=True,
        requires_attendance=True,
        date_time__lt=now,
    ).distinct()

    if start_date:
        parent_events = parent_events.filter(date_time__gte=start_date)

    for parent in parent_events:
        # Check if user can see this event
        if not parent.is_visible_to_user(request.user):
            continue

        # Get all instances including parent
        series_events = Event.objects.filter(
            Q(pk=parent.pk) | Q(parent_event=parent),
            date_time__lt=now
        )
        if start_date:
            series_events = series_events.filter(date_time__gte=start_date)

        series_attendance = Attendance.objects.filter(
            user=request.user,
            event__in=series_events,
            attendance_type='event'
        )

        series_total = series_attendance.count()
        if series_total > 0:
            series_present = series_attendance.filter(status__in=['present', 'late']).count()
            series_rate = round((series_present / series_total) * 100, 1)

            series_stats.append({
                'title': parent.title,
                'event_count': series_events.count(),
                'attended_count': series_present,
                'attendance_rate': series_rate,
            })

    # Sort by rate
    series_stats.sort(key=lambda x: x['attendance_rate'], reverse=True)

    context = {
        'my_stats': my_stats,
        'chapter_average': chapter_average,
        'rate_difference': rate_difference,
        'attendance_history': attendance_history,
        'series_stats': series_stats,
        'range_filter': range_filter,
    }

    return render(request, 'my_attendance.html', context)
