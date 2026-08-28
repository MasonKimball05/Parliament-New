"""
Member-facing QR self-check-in for event attendance.

v3.27.0 — the scan target for the QR code an officer displays via
src/view/officer/event_attendance.py's manage_qr_checkin/qr_checkin_image.
See EventCheckinWindow (src/models/events.py) for the security model: a
window an officer opens by hand, good for a short, fixed time.

This view is deliberately login_required ONLY — no officer_required, no
member_type check beyond "is this an active member" — because the whole
point is that any member in the room can scan for themselves. It is NOT
login-exempt: an anonymous scan would record nobody's attendance and would
mean the QR content alone (rather than the QR content plus being a logged-in
member) is what attests presence, which is a weaker claim than "this specific
member's account scanned this code."
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from src.feature_flag_decorators import check_feature_enabled, require_feature_flag
from src.models import (
    ActivityLog, Attendance, Event, EventCheckinEmbed, EventCheckinWindow,
)


@login_required
@require_feature_flag('attendance_tracking', 'event_attendance', 'qr_attendance_checkin')
def event_qr_checkin(request, event_id, token):
    """
    Where a scanned QR code lands. Marks the CURRENT user present for the
    event if `token` matches an open EventCheckinWindow — never anyone else's
    attendance, and never anything an officer's own marking can't
    subsequently override.
    """
    event = get_object_or_404(Event, id=event_id, requires_attendance=True)

    context = {'event': event}

    if event.attendance_finalized:
        context['result'] = 'finalized'
        return render(request, 'event_qr_checkin_result.html', context, status=403)

    if request.user.member_status != 'Active':
        # Mirrors mark_event_attendance's own member list
        # (ParliamentUser.objects.filter(member_status='Active')) — a pledge,
        # alumnus, or removed member scanning a valid code should not create
        # an attendance row that officer marking would never have created for
        # them either.
        context['result'] = 'not_eligible'
        return render(request, 'event_qr_checkin_result.html', context, status=403)

    window = EventCheckinWindow.objects.filter(event=event, token=token).first()
    if not window or not window.is_open():
        context['result'] = 'expired'
        return render(request, 'event_qr_checkin_result.html', context, status=410)

    attendance, created = Attendance.objects.update_or_create(
        event=event,
        user=request.user,
        attendance_type='event',
        defaults={
            'status': 'present',
            'marked_by': request.user,
            'marked_at': timezone.now(),
            'notes': (
                'Self-checked in via QR '
                f'(window opened by {window.opened_by.name if window.opened_by else "an officer"})'
            ),
        },
    )

    ActivityLog.log_activity(
        action_type='attendance_taken',
        user=request.user,
        description=f'{request.user.name} self-checked in to {event.title} via QR',
        request=request,
        object_type='Event',
        object_id=event.id,
        object_repr=str(event),
    )

    context['result'] = 'success'
    context['already_checked_in'] = not created
    return render(request, 'event_qr_checkin_result.html', context)


def event_checkin_embed_image(request, event_id, embed_token):
    """
    The public, no-login embed image — for pasting into a slide deck. See
    EventCheckinEmbed's docstring (src/models/events.py) for the full
    reasoning on why this is safe to be unauthenticated.

    Deliberately NOT @login_required and NOT @require_feature_flag — a
    slideshow fetching this has no session, so a redirect-to-login or a
    rendered "feature disabled" page would just be a broken image in the
    deck. Both conditions are checked by hand below and answered with a
    plain 404 or the waiting placeholder instead.
    """
    if not check_feature_enabled('qr_attendance_checkin'):
        return HttpResponse(status=404)

    event = get_object_or_404(Event, id=event_id, requires_attendance=True)
    embed = EventCheckinEmbed.objects.filter(
        event=event, token=embed_token, revoked_at__isnull=True,
    ).first()
    if not embed:
        return HttpResponse(status=404)

    from src.utils.qr_svg import WAITING_PLACEHOLDER_SVG, render_qr_svg

    window = EventCheckinWindow.get_open_window(event)
    if not window:
        response = HttpResponse(WAITING_PLACEHOLDER_SVG, content_type='image/svg+xml')
    else:
        checkin_url = request.build_absolute_uri(
            reverse('event_qr_checkin', args=[event.id, window.token])
        )
        response = HttpResponse(render_qr_svg(checkin_url), content_type='image/svg+xml')

    # No caching anywhere in the chain — the whole point of a stable embed
    # link is that the SAME url shows a different thing (or nothing) minute
    # to minute. A cached "waiting" placeholder would keep showing after a
    # window opens; a cached QR would keep showing after it expires.
    response['Cache-Control'] = 'no-store'
    return response
