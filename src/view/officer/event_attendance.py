"""
Event-based attendance management for officers
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta
from src.models import Event, Attendance, ParliamentUser, AttendanceExcuse, ActivityLog
from src.decorators import officer_required
from src.feature_flag_decorators import require_feature_flag


@login_required
@require_feature_flag('attendance_tracking')
@officer_required
def event_attendance_list(request):
    """
    List all events that require attendance tracking
    """
    # Get events that require attendance
    events = Event.objects.filter(requires_attendance=True).order_by('-date_time')

    # Separate into upcoming and past
    now = timezone.now()
    upcoming_events = events.filter(date_time__gte=now, attendance_finalized=False)
    past_events = events.filter(Q(date_time__lt=now) | Q(attendance_finalized=True))[:20]  # Last 20

    # Get counts of pending excuses
    pending_excuses_count = AttendanceExcuse.objects.filter(status='pending').count()

    context = {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'pending_excuses_count': pending_excuses_count,
    }

    return render(request, 'officer/event_attendance_list.html', context)


@login_required
@require_feature_flag('attendance_tracking')
@officer_required
def mark_event_attendance(request, event_id):
    """
    Mark attendance for a specific event
    Shows read-only view for finalized events
    """
    event = get_object_or_404(Event, id=event_id, requires_attendance=True)

    # Check if attendance is finalized - show read-only view
    is_read_only = event.attendance_finalized

    # Get all active members
    members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # Get existing attendance records (only event attendance)
    existing_attendance = {
        att.user_id: att
        for att in Attendance.objects.filter(event=event, attendance_type='event').select_related('user')
    }

    # Get all excuse requests for this event
    all_excuses = {
        exc.user_id: exc
        for exc in AttendanceExcuse.objects.filter(event=event).select_related('user')
    }

    # Get approved excuses for this event
    approved_excuses = {
        exc.user_id: exc
        for exc in AttendanceExcuse.objects.filter(event=event, status='approved').select_related('user')
    }

    if request.method == 'POST' and not is_read_only:
        action = request.POST.get('action')

        if action == 'mark_attendance':
            # Get lists from form
            present_ids = request.POST.getlist('present')
            absent_ids = request.POST.getlist('absent')
            late_ids = request.POST.getlist('late')

            updated_count = 0

            # Get manual excuse IDs
            excuse_manual_ids = request.POST.getlist('excuse_manual')

            for member in members:
                # Skip if already excused via approved excuse
                if member.user_id in approved_excuses:
                    continue

                # Check if manually excused by officer
                if member.user_id in excuse_manual_ids:
                    excuse_reason_text = request.POST.get(f'excuse_reason_{member.user_id}', '').strip()
                    if excuse_reason_text:
                        attendance, created = Attendance.objects.update_or_create(
                            event=event,
                            user=member,
                            attendance_type='event',
                            defaults={
                                'status': 'excused',
                                'marked_by': request.user,
                                'marked_at': timezone.now(),
                                'notes': f'Officer excused: {excuse_reason_text}'
                            }
                        )
                        updated_count += 1
                        continue

                # Determine status
                if member.user_id in present_ids:
                    status = 'present'
                elif member.user_id in late_ids:
                    status = 'late'
                elif member.user_id in absent_ids:
                    status = 'absent'
                else:
                    continue  # Skip if not marked

                # Create or update attendance
                attendance, created = Attendance.objects.update_or_create(
                    event=event,
                    user=member,
                    attendance_type='event',
                    defaults={
                        'status': status,
                        'marked_by': request.user,
                        'marked_at': timezone.now()
                    }
                )
                updated_count += 1

            # Log activity
            ActivityLog.log_activity(
                action_type='attendance_taken',
                user=request.user,
                description=f'Marked attendance for {event.title} ({updated_count} members)',
                request=request,
                object_type='Event',
                object_id=event.id,
                object_repr=str(event)
            )

            messages.success(request, f'Attendance updated for {updated_count} members.')
            return redirect('mark_event_attendance', event_id=event.id)

        elif action == 'finalize':
            # Finalize attendance
            event.attendance_finalized = True
            event.finalized_by = request.user
            event.finalized_at = timezone.now()
            event.save()

            # Log activity
            ActivityLog.log_activity(
                action_type='attendance_taken',
                user=request.user,
                description=f'Finalized attendance for {event.title}',
                request=request,
                object_type='Event',
                object_id=event.id,
                object_repr=str(event)
            )

            messages.success(request, f'Attendance for "{event.title}" has been finalized.')
            return redirect('event_attendance_list')

    # Build member data with current status
    member_data = []
    for member in members:
        # Check if has any excuse request
        excuse = all_excuses.get(member.user_id)
        has_approved_excuse = member.user_id in approved_excuses

        # Get current attendance status
        attendance = existing_attendance.get(member.user_id)
        current_status = attendance.status if attendance else ('excused' if has_approved_excuse else 'pending')

        # Prepare excuse information
        excuse_status = None
        excuse_reason = None
        excuse_full_reason = None
        excuse_submitted_at = None
        excuse_document_url = None
        if excuse:
            excuse_status = excuse.status
            excuse_reason = excuse.reason[:50] + '...' if len(excuse.reason) > 50 else excuse.reason
            excuse_full_reason = excuse.reason
            excuse_submitted_at = excuse.submitted_at
            if excuse.supporting_document:
                excuse_document_url = excuse.supporting_document.url

        member_data.append({
            'user': member,
            'current_status': current_status,
            'has_excuse': has_approved_excuse,
            'excuse_status': excuse_status,  # Will be 'pending', 'approved', 'denied', or None
            'excuse_reason': excuse_reason,
            'excuse_full_reason': excuse_full_reason,
            'excuse_submitted_at': excuse_submitted_at,
            'excuse_document_url': excuse_document_url,
            'attendance_record': attendance
        })

    # Get attendance statistics
    stats = event.get_attendance_stats()

    context = {
        'event': event,
        'member_data': member_data,
        'stats': stats,
        'can_finalize': not event.attendance_finalized,
        'is_read_only': is_read_only,
    }

    return render(request, 'officer/mark_event_attendance.html', context)


@login_required
@require_feature_flag('attendance_tracking')
@officer_required
def review_excuses(request, event_id=None):
    """
    Review pending excuse requests
    Shows all events with their excuses grouped together
    """
    # Get filter parameters
    status_filter = request.GET.get('status', 'all')
    show_archived = request.GET.get('show_archived', 'false') == 'true'

    # Handle excuse review actions first
    if request.method == 'POST':
        excuse_id = request.POST.get('excuse_id')
        action = request.POST.get('action')
        notes = request.POST.get('notes', '')

        if excuse_id and action:
            excuse = get_object_or_404(AttendanceExcuse, id=excuse_id)

            if action == 'approve':
                excuse.approve(request.user, notes)
                messages.success(request, f'Approved excuse for {excuse.user.name}')

                # Log activity
                ActivityLog.log_activity(
                    action_type='other',
                    user=request.user,
                    description=f'Approved excuse for {excuse.user.name} for {excuse.event.title}',
                    request=request,
                    object_type='AttendanceExcuse',
                    object_id=excuse.id,
                    object_repr=str(excuse)
                )

            elif action == 'deny':
                excuse.deny(request.user, notes)
                messages.warning(request, f'Denied excuse for {excuse.user.name}')

                # Log activity
                ActivityLog.log_activity(
                    action_type='other',
                    user=request.user,
                    description=f'Denied excuse for {excuse.user.name} for {excuse.event.title}',
                    request=request,
                    object_type='AttendanceExcuse',
                    object_id=excuse.id,
                    object_repr=str(excuse)
                )

            # Redirect back with filters preserved
            redirect_url = '/officers/excuses/'
            params = []
            if status_filter and status_filter != 'all':
                params.append(f'status={status_filter}')
            if show_archived:
                params.append('show_archived=true')
            if params:
                redirect_url += '?' + '&'.join(params)
            return redirect(redirect_url)

    # Get all events with excuse requests
    now = timezone.now()
    events_query = Event.objects.filter(
        excuse_requests__isnull=False
    ).distinct()

    if not show_archived:
        # Show only upcoming or recent events (within last 30 days)
        cutoff_date = now - timedelta(days=30)
        events_query = events_query.filter(date_time__gte=cutoff_date)

    events = events_query.order_by('-date_time')

    # Build events with their excuses
    events_with_excuses = []
    for event in events:
        # Get excuses for this event
        event_excuses = AttendanceExcuse.objects.filter(
            event=event
        ).select_related('user', 'reviewed_by').order_by('-submitted_at')

        # Apply status filter
        if status_filter and status_filter != 'all':
            filtered_excuses = event_excuses.filter(status=status_filter)
        else:
            filtered_excuses = event_excuses

        # Only include events that have excuses matching the filter
        if filtered_excuses.exists() or status_filter == 'all':
            events_with_excuses.append({
                'event': event,
                'excuses': list(filtered_excuses),
                'pending_count': event_excuses.filter(status='pending').count(),
                'approved_count': event_excuses.filter(status='approved').count(),
                'denied_count': event_excuses.filter(status='denied').count(),
                'total_count': event_excuses.count(),
                'is_past': event.date_time < now,
            })

    # Filter out events with no matching excuses when filtering
    if status_filter and status_filter != 'all':
        events_with_excuses = [e for e in events_with_excuses if e['excuses']]

    # Get overall statistics
    stats = {
        'pending': AttendanceExcuse.objects.filter(status='pending').count(),
        'approved': AttendanceExcuse.objects.filter(status='approved').count(),
        'denied': AttendanceExcuse.objects.filter(status='denied').count(),
        'total': AttendanceExcuse.objects.count(),
    }

    context = {
        'events_with_excuses': events_with_excuses,
        'selected_status': status_filter,
        'show_archived': show_archived,
        'stats': stats,
    }

    return render(request, 'officer/review_excuses.html', context)
