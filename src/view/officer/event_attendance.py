"""
Event-based attendance management for officers
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse
from django.utils import timezone
from django.db.models import Q
from django.urls import reverse
from datetime import datetime, timedelta
from src.models import (
    Event, Attendance, ParliamentUser, AttendanceExcuse, ActivityLog,
    EventCheckinWindow, EventCheckinEmbed,
)
from src.decorators import officer_required
from src.feature_flag_decorators import require_feature_flag, check_feature_enabled
from src.models.users import member_defer


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance')
@officer_required
def event_attendance_list(request):
    """
    List all events that require attendance tracking
    """
    # Get events that require attendance; prefetch service_event so the template
    # can detect service events without extra per-row queries.
    events = Event.objects.filter(requires_attendance=True).select_related('service_event').order_by('-date_time')

    # Separate into upcoming and past
    now = timezone.now()
    upcoming_events = events.filter(date_time__gte=now, attendance_finalized=False)
    past_events = events.filter(Q(date_time__lt=now) | Q(attendance_finalized=True))[:20]  # Last 20

    # ⚠️ v3.25.0 — `event_attendance_list.html` calls `event.get_attendance_stats`
    # inside BOTH the desktop table and the mobile card list, so twenty past
    # events meant forty calls at six queries each. Measured through the real
    # endpoint: 271 queries, 240 of them this. Priming the cache here makes it
    # two, and the memoisation in the method itself collapses the duplicate
    # layout.
    #
    # ⚠️ `prime_attendance_stats` RETURNS A LIST and this must keep the list.
    # The cache lives on each instance; re-evaluating the queryset afterwards
    # would give the template different objects with an empty cache and restore
    # the N+1 in a way no assertion here would notice.
    past_events = Event.prime_attendance_stats(past_events)

    # Get counts of pending excuses
    pending_excuses_count = AttendanceExcuse.objects.filter(status='pending').count()

    context = {
        'upcoming_events': upcoming_events,
        'past_events': past_events,
        'pending_excuses_count': pending_excuses_count,
    }

    return render(request, 'officer/event_attendance_list.html', context)


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance')
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
        for att in Attendance.objects.filter(event=event, attendance_type='event').select_related('user').defer(*member_defer('user'))
    }

    # Get all excuse requests for this event
    all_excuses = {
        exc.user_id: exc
        for exc in AttendanceExcuse.objects.filter(event=event).select_related('user').defer(*member_defer('user'))
    }

    # Get approved excuses for this event
    approved_excuses = {
        exc.user_id: exc
        for exc in AttendanceExcuse.objects.filter(event=event, status='approved').select_related('user').defer(*member_defer('user'))
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
                # v3.19.6 — the ownership-aware route, not `/media/`. This was
                # `.url`, i.e. `/media/excuse_documents/<slug>.pdf`, which
                # `serve_media` now refuses. The document is a doctor's note;
                # see `src/view/serve_private_upload.py`.
                excuse_document_url = reverse('excuse_document', args=[excuse.id])

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

    # v3.27.0 — QR self-check-in is additive to everything above; nothing in
    # this view's own marking logic is gated on it. The link to the QR
    # management page is only shown if the flag is actually on, and
    # `qr_open_window` lets the template say "a window is currently open"
    # without a second round trip once the officer is already on this page.
    qr_checkin_enabled = check_feature_enabled('qr_attendance_checkin')
    qr_open_window = (
        EventCheckinWindow.get_open_window(event) if qr_checkin_enabled else None
    )

    context = {
        'event': event,
        'member_data': member_data,
        'stats': stats,
        'can_finalize': not event.attendance_finalized,
        'is_read_only': is_read_only,
        'qr_checkin_enabled': qr_checkin_enabled,
        'qr_open_window': qr_open_window,
    }

    return render(request, 'officer/mark_event_attendance.html', context)


@login_required
@require_feature_flag('attendance_tracking', 'excuse_system')
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
        ).select_related('user', 'reviewed_by').defer(*member_defer('user', 'reviewed_by')).order_by('-submitted_at')

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


# ---------------------------------------------------------------------------
# v3.27.0 — QR self-check-in. Officer-facing: open/close a window, view the
# code. See EventCheckinWindow's docstring (src/models/events.py) for why this
# is time-boxed and why it never touches the manual marking logic above.
# ---------------------------------------------------------------------------

@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def manage_qr_checkin(request, event_id):
    """Show the current (or most recently open) QR check-in window for an
    event, with controls to open a new one or close the current one early."""
    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    window = EventCheckinWindow.get_open_window(event)
    embed = EventCheckinEmbed.objects.filter(event=event, revoked_at__isnull=True).first()

    embed_url = None
    if embed:
        embed_url = request.build_absolute_uri(
            reverse('event_checkin_embed_image', args=[event.id, embed.token])
        )

    return render(request, 'officer/manage_qr_checkin.html', {
        'event': event,
        'window': window,
        'embed_url': embed_url,
    })


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def open_qr_checkin(request, event_id):
    """Start a new 15-minute check-in window for an event."""
    if request.method != 'POST':
        return redirect('manage_qr_checkin', event_id=event_id)

    event = get_object_or_404(Event, id=event_id, requires_attendance=True)

    if event.attendance_finalized:
        messages.error(request, 'Attendance for this event has already been finalized.')
        return redirect('manage_qr_checkin', event_id=event.id)

    window = EventCheckinWindow.open_for(event, opened_by=request.user)

    ActivityLog.log_activity(
        action_type='attendance_taken',
        user=request.user,
        description=(
            f'{request.user.name} opened a {EventCheckinWindow.WINDOW_MINUTES}-minute '
            f'QR check-in window for {event.title}'
        ),
        request=request,
        object_type='Event',
        object_id=event.id,
        object_repr=str(event),
    )

    messages.success(
        request,
        f'QR check-in is open for {EventCheckinWindow.WINDOW_MINUTES} minutes.'
    )
    return redirect('manage_qr_checkin', event_id=event.id)


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def close_qr_checkin(request, event_id):
    """End the currently open window early."""
    if request.method != 'POST':
        return redirect('manage_qr_checkin', event_id=event_id)

    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    window = EventCheckinWindow.get_open_window(event)

    if window:
        window.closed_early_at = timezone.now()
        window.closed_early_by = request.user
        window.save(update_fields=['closed_early_at', 'closed_early_by'])
        messages.success(request, 'QR check-in window closed.')
    else:
        messages.info(request, 'There is no open QR check-in window for this event.')

    return redirect('manage_qr_checkin', event_id=event.id)


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def qr_checkin_image(request, event_id):
    """
    The QR code itself, as SVG — same technique as the TOTP enrolment QR in
    src/view/two_factor.py (qrcode.make(..., image_factory=SvgPathImage)).

    Gated the same as the rest of this window (officer-only) — not because
    the image is more sensitive than the projected screen everyone in the
    room can already see, but so a bookmarked image URL doesn't keep working
    for a non-officer after the fact. The real security boundary is the
    token's 15-minute expiry inside the QR's own encoded URL, which holds
    regardless of who fetched this image or how.
    """
    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    window = EventCheckinWindow.get_open_window(event)
    if not window:
        return HttpResponse(status=404)

    from src.utils.qr_svg import render_qr_svg

    checkin_url = request.build_absolute_uri(
        reverse('event_qr_checkin', args=[event.id, window.token])
    )
    response = HttpResponse(render_qr_svg(checkin_url), content_type='image/svg+xml')
    response['Cache-Control'] = 'no-store'  # this SVG encodes a live, still-valid token
    return response


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def generate_qr_embed_link(request, event_id):
    """
    Create (or reuse) the stable, unauthenticated embed link for this event,
    then send the officer back to the management page where it's displayed.
    See EventCheckinEmbed's docstring for why this can't just reuse
    qr_checkin_image's own login-gated URL: a slide deck fetches images with
    no session at all.
    """
    if request.method != 'POST':
        return redirect('manage_qr_checkin', event_id=event_id)

    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    EventCheckinEmbed.get_or_create_for(event, created_by=request.user)

    messages.success(request, 'Embed link ready — copy it into your slides.')
    return redirect('manage_qr_checkin', event_id=event.id)


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
@officer_required
def revoke_qr_embed_link(request, event_id):
    """Kill the current embed link (e.g. it leaked somewhere unintended).
    A fresh one can be generated afterward via generate_qr_embed_link, which
    is a NEW token — anything using the old URL stops working immediately."""
    if request.method != 'POST':
        return redirect('manage_qr_checkin', event_id=event_id)

    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    embed = EventCheckinEmbed.objects.filter(event=event, revoked_at__isnull=True).first()
    if embed:
        embed.revoke()
        messages.success(request, 'Embed link revoked.')
    else:
        messages.info(request, 'There is no active embed link for this event.')

    return redirect('manage_qr_checkin', event_id=event.id)
