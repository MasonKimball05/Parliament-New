"""
Admin v2: ⌘K search and the member page (v3.58.1).

The member page puts everything about one member on one screen: profile,
roles and committees, two-factor and passkeys, web sessions and app devices
(each revocable), recent sign-ins, lockouts, watch flag and recent activity.
Before, that was spread over the Users list, Edit profile, Login security,
Lockouts and the activity log. The deeper pages are still linked for detail.
"""
from datetime import timedelta

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.admin_v2_nav import NAV, search_pages
from src.kai_audit import exclude_kai_logs
from src.models import ActivityLog, LoginHistory, ParliamentUser
from src.models.mobile import MobileDevice
from src.models.security import LoginLockout, UserWatchFlag
from src.models.users import UserSession
from src.models.webauthn import WebAuthnCredential
from src.view.admin_v2 import require_admin_v2_auth

MEMBER_RESULTS = 8


def _member_meta(u):
    bits = [f'#{u.user_id}', u.member_type, u.member_status]
    if u.is_admin:
        bits.append('admin')
    return ' · '.join(b for b in bits if b)


@require_admin_v2_auth
def admin_v2_search(request):
    """GET ?q= → {'pages': [...], 'members': [...]} for the ⌘K palette."""
    q = (request.GET.get('q') or '').strip()[:80]
    if not q:
        # Nothing typed yet: one starting point per section.
        pages = []
        for title, items in NAV:
            for label, name, _also, _kw in items[:1]:
                pages.extend(p for p in search_pages(label) if p['label'] == label)
        return JsonResponse({'pages': pages, 'members': []})

    term = q.lstrip('#')
    cond = (Q(name__icontains=term) | Q(username__icontains=term)
            | Q(preferred_name__icontains=term) | Q(email__icontains=term))
    if term.isdigit():
        cond |= Q(user_id=term)
    users = (ParliamentUser.objects.filter(cond)
             .only('user_id', 'name', 'preferred_name', 'username', 'member_type', 'member_status', 'is_admin')
             .order_by('name')[:MEMBER_RESULTS])
    members = [{
        'name': u.get_display_name(),
        'meta': _member_meta(u),
        'url': reverse('admin_v2_member', args=[u.user_id]),
    } for u in users]
    return JsonResponse({'pages': search_pages(q), 'members': members, 'content': search_content(term)})


CONTENT_RESULTS = 6


def search_content(term):
    """Events, announcements and chapter legislation by title (titles only, newest first)."""
    from src.models import Announcement, Event, Legislation

    if len(term) < 2:
        return []
    hits = []
    for e in Event.objects.filter(title__icontains=term).only('pk', 'title', 'date_time').order_by('-date_time')[:4]:
        hits.append({'kind': 'Event', 'label': e.title, 'meta': f'Event · {e.date_time:%b %-d, %Y}',
                     'url': reverse('edit_event', args=[e.pk]), 'when': e.date_time})
    for a in (Announcement.objects.filter(title__icontains=term).only('pk', 'title', 'posted_at')
              .order_by('-posted_at')[:4]):
        hits.append({'kind': 'Announcement', 'label': a.title, 'meta': f'Announcement · {a.posted_at:%b %-d, %Y}',
                     'url': reverse('edit_announcement', args=[a.pk]), 'when': a.posted_at})
    for leg in (Legislation.objects.filter(title__icontains=term).only('pk', 'title', 'status', 'available_at')
                .order_by('-available_at')[:4]):
        hits.append({'kind': 'Legislation', 'label': leg.title,
                     'meta': f'Legislation · {leg.get_status_display()}',
                     'url': reverse('legislation_detail', args=[leg.pk]), 'when': leg.available_at})
    hits.sort(key=lambda h: h['when'] or timezone.now(), reverse=True)
    return [{k: h[k] for k in ('label', 'meta', 'url')} for h in hits[:CONTENT_RESULTS]]


@require_admin_v2_auth
def admin_v2_member(request, user_id):
    member = get_object_or_404(ParliamentUser, user_id=user_id)
    now = timezone.now()

    from django_otp.plugins.otp_totp.models import TOTPDevice
    has_totp = TOTPDevice.objects.filter(user=member, confirmed=True).exists()
    passkeys = list(WebAuthnCredential.objects.filter(user=member)
                    .only('name', 'created_at', 'last_used_at').order_by('-created_at'))

    devices = list(MobileDevice.objects.filter(user=member).order_by('revoked_at', '-last_seen_at', '-created_at')[:20])
    sessions = list(UserSession.objects.filter(user=member).order_by('-last_activity')[:20])

    logins = list(LoginHistory.objects.filter(user=member).order_by('-timestamp')[:10])
    month_ago = now - timedelta(days=30)
    failed_30d = LoginHistory.objects.filter(user=member, status='failed', timestamp__gte=month_ago).count()

    lockouts = [lo for lo in LoginLockout.objects.filter(username__iexact=member.username, is_cleared=False)
                .order_by('-locked_at')[:5] if lo.is_active()]

    watch = UserWatchFlag.objects.filter(user=member, is_active=True).first()
    activity = list(exclude_kai_logs(ActivityLog.objects.filter(user=member), request.user).order_by('-timestamp')[:15])

    context = {
        'member': member,
        'roles': list(member.roles.all().order_by('name')),
        'committees': list(member.committees.all().order_by('name')),
        'chaired': list(member.chair_roles.all().order_by('name')),
        'has_totp': has_totp,
        'passkeys': passkeys,
        'devices': devices,
        'active_devices': sum(1 for d in devices if d.revoked_at is None),
        'sessions': sessions,
        'logins': logins,
        'failed_30d': failed_30d,
        'lockouts': lockouts,
        'watch': watch,
        'activity': activity,
        'chapter': chapter_summary(member),
    }
    return render(request, 'admin_v2/member.html', context)


def chapter_summary(member):
    """
    Attendance (last 90 days, the same rate as My attendance), excuses waiting
    for review, and service hours for the current period (the same numbers as
    the member's Service hours page). No vote participation: some votes are
    anonymous, and who voted is not admin-page material.
    """
    from src.models import Attendance, AttendanceExcuse, ServicePeriod
    from src.view.service_user_dashboard import get_user_service_stats

    now = timezone.now()
    records = list(Attendance.objects.filter(
        user=member, attendance_type='event', event__requires_attendance=True, event__is_active=True,
        event__date_time__lt=now, event__date_time__gte=now - timedelta(days=90),
    ).values_list('status', flat=True))
    total = len(records)
    counts = {s: records.count(s) for s in ('present', 'late', 'absent', 'excused')}
    attendance = {
        'total': total, **counts,
        'rate': round((counts['present'] + counts['late']) / total * 100) if total else None,
    }
    excuses_pending = AttendanceExcuse.objects.filter(user=member, status='pending').count()

    today = timezone.localdate()
    periods = ServicePeriod.objects.filter(is_active=True).order_by('-start_date')
    period = periods.filter(start_date__lte=today, end_date__gte=today).first() or periods.first()
    service = get_user_service_stats(member, period) if period else None
    return {'attendance': attendance, 'excuses_pending': excuses_pending,
            'service': service, 'service_period': period}


def _log(request, member, description):
    ActivityLog.log_activity(
        action_type='user_edited',
        user=request.user,
        description=description,
        object_type='ParliamentUser',
        object_id=str(member.user_id),
        object_repr=member.get_display_name(),
        request=request,
    )


@require_admin_v2_auth
@require_POST
def admin_v2_member_sign_out(request, user_id):
    """End every web session and sign out every app install of this member."""
    member = get_object_or_404(ParliamentUser, user_id=user_id)
    devices_before = MobileDevice.objects.filter(user=member, revoked_at__isnull=True).count()
    keep = request.session.session_key if member.pk == request.user.pk else None
    sessions = UserSession.revoke_other_sessions(member, keep_session_key=keep)
    _log(request, member, f'{request.user.get_display_name()} signed {member.get_display_name()} out '
                          f'everywhere ({sessions} web session(s), {devices_before} app device(s))')
    messages.success(request, f'Signed {member.get_display_name()} out of {sessions} web session(s) '
                              f'and their app.')
    return redirect('admin_v2_member', user_id=member.user_id)


@require_admin_v2_auth
@require_POST
def admin_v2_member_revoke_device(request, user_id, device_id):
    member = get_object_or_404(ParliamentUser, user_id=user_id)
    device = get_object_or_404(MobileDevice, public_id=device_id, user=member)
    if device.revoked_at is None:
        device.revoke(f'revoked by admin {request.user.user_id}')
        _log(request, member, f'{request.user.get_display_name()} signed out {member.get_display_name()}\'s '
                              f'{device.get_platform_display()} app ({device.device_name or "unnamed device"})')
        messages.success(request, 'That app install is signed out.')
    return redirect('admin_v2_member', user_id=member.user_id)
