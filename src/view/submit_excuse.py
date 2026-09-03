"""
Member-facing views for attendance and excuse requests
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Count, Q
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError
from datetime import timedelta

from src.models import Event, Attendance, AttendanceExcuse, ActivityLog, ParliamentUser
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_feature_flag
from src.models.users import member_defer


@login_required
@require_feature_flag('attendance_tracking', 'excuse_system')
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

    # v3.29.4 — a committee event only expects its own required members
    # (committee + sign-ups) to attend, so it doesn't make sense to offer
    # an excuse to someone outside that set. Chapter-wide events are
    # unaffected (`user_is_required` is always True for them).
    available_events = [event for event in available_events if event.user_is_required(request.user)]

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
@require_feature_flag('attendance_tracking', 'excuse_system')
def submit_excuse(request, event_id):
    """
    Submit an excuse request for a specific event
    """
    event = get_object_or_404(Event, id=event_id, is_active=True)

    # Check if user can see this event
    if not event.is_visible_to_user(request.user):
        messages.error(request, 'You do not have access to this event.')
        return redirect('my_excuses')

    # v3.29.4 — a committee event only expects its own required members
    # (committee + sign-ups) to attend; block direct-URL submission by
    # someone outside that set the same way the list above already hides
    # it from them.
    if not event.user_is_required(request.user):
        messages.error(request, 'This event is not one you are required to attend.')
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
@require_feature_flag('attendance_tracking', 'excuse_system')
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
@require_feature_flag('attendance_tracking', 'event_attendance')
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

    # ⚠️ v3.24.0 — READ ONCE, COUNT IN PYTHON. This block used to be five
    # separate `COUNT(*)` queries (total, present, late, absent, excused) over a
    # queryset whose `event__in=events` clause inlines one bind parameter per
    # event, and then the history loop below re-queried the same rows one event
    # at a time. Measured on the pre-fix code: 40 events → **116 queries**;
    # 120 events with the "All time" filter (`?range=0`, one click) → **349**.
    #
    # Nobody had noticed because until v3.22.0 this page was linked from
    # nowhere and the only way to reach it was to type the URL. v3.22.0 put it
    # on the home page of every member and on the My Excuses header — which is
    # the right call, and it is what turned an unreachable page's cost into the
    # chapter's cost. **A page's query count only starts mattering on the day
    # somebody can click it**, so promoting a page is a performance change.
    #
    # The rows are all needed by the history loop anyway, so evaluating the
    # queryset once and counting in Python is strictly less work than asking the
    # database the same question five times.
    event_ids = [e.pk for e in events]
    my_records = list(
        Attendance.objects
        .filter(user=request.user, event_id__in=event_ids, attendance_type='event')
        .select_related('event')
    )
    record_by_event = {r.event_id: r for r in my_records}

    total = len(my_records)
    if total > 0:
        present = sum(1 for r in my_records if r.status == 'present')
        late = sum(1 for r in my_records if r.status == 'late')
        absent = sum(1 for r in my_records if r.status == 'absent')
        excused = sum(1 for r in my_records if r.status == 'excused')
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

    # One aggregate rather than two counts. `filter=` on a `Count` is the
    # conditional-aggregate form, so both numbers come back in one row.
    chapter_totals = Attendance.objects.filter(
        event_id__in=event_ids,
        attendance_type='event',
    ).aggregate(
        total=Count('id'),
        attended=Count('id', filter=Q(status__in=['present', 'late'])),
    )
    chapter_total = chapter_totals['total'] or 0
    if chapter_total > 0:
        chapter_average = round((chapter_totals['attended'] / chapter_total) * 100, 1)
    else:
        chapter_average = 0

    # Calculate difference from chapter average
    if my_stats['total'] > 0 and chapter_total > 0:
        rate_difference = round(my_stats['attendance_rate'] - chapter_average, 1)
    else:
        rate_difference = 0

    # ⚠️ v3.24.0 — TWO QUERIES, NOT TWO PER EVENT. Both lookups in this loop
    # were `.filter(...).first()` inside the loop body, so the page cost 2N
    # queries for N events — and the attendance half was querying rows the view
    # had already fetched three lines above.
    excuse_by_event = {
        ex.event_id: ex
        for ex in AttendanceExcuse.objects.filter(
            user=request.user, event_id__in=event_ids,
        )
    }

    attendance_history = []
    for event in events:
        record = record_by_event.get(event.pk)
        attendance_history.append({
            'event': event,
            'status': record.status if record else 'not_marked',
            'excuse': excuse_by_event.get(event.pk),
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

    # ⚠️ v3.24.0 — TWO QUERIES FOR EVERY SERIES, not three per series. The loop
    # below used to run `series_events` + two `COUNT(*)`s + `series_events
    # .count()` once per recurring parent. The set of series is small, so this
    # was never the dominant cost — but it grows with the chapter's calendar and
    # it is the same defect as the history loop one screen up.
    #
    # ⚠️ THE FILTERS HERE ARE DELIBERATELY NOT THE ONES USED FOR `events` ABOVE.
    # A series instance is counted whether or not it is `is_active` and whether
    # or not it individually sets `requires_attendance`; only the PARENT is
    # tested for those. That is what the old code did, and changing it would
    # silently restate every member's per-series percentage — a different change
    # from making the page fast, and not one to make in the same diff.
    visible_parents = [
        p for p in parent_events if p.is_visible_to_user(request.user)
    ]
    parent_ids = [p.pk for p in visible_parents]

    if parent_ids:
        series_rows = Event.objects.filter(
            Q(pk__in=parent_ids) | Q(parent_event_id__in=parent_ids),
            date_time__lt=now,
        )
        if start_date:
            series_rows = series_rows.filter(date_time__gte=start_date)

        # A parent has `parent_event__isnull=True` by the filter above, so a row
        # is keyed by its parent when it has one and by itself when it is one.
        event_ids_by_parent = {}
        for pk, parent_pk in series_rows.values_list('pk', 'parent_event_id'):
            event_ids_by_parent.setdefault(parent_pk or pk, []).append(pk)

        all_series_ids = [
            pk for ids in event_ids_by_parent.values() for pk in ids
        ]
        status_by_event = dict(
            Attendance.objects
            .filter(user=request.user, event_id__in=all_series_ids,
                    attendance_type='event')
            .values_list('event_id', 'status')
        )

        for parent in visible_parents:
            ids = event_ids_by_parent.get(parent.pk, [])
            marks = [status_by_event[pk] for pk in ids if pk in status_by_event]
            series_total = len(marks)
            if series_total > 0:
                series_present = sum(
                    1 for status in marks if status in ('present', 'late')
                )
                series_stats.append({
                    'title': parent.title,
                    'event_count': len(ids),
                    'attended_count': series_present,
                    'attendance_rate': round(
                        (series_present / series_total) * 100, 1),
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
