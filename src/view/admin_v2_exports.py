"""
Admin v2: exports (v3.58.1).

One page for every download an admin needs. Three CSVs are new here (the
member roster with admin columns, attendance per member, service hours per
member); the rest are links to exports that already exist elsewhere (the
directory, the officer user list, activity logs, service submissions, the
calendar), so each keeps its own rules. Kai's export is deliberately not
listed: it belongs to the Kai committee's pages.

Every CSV goes through safe_csv_writer (spreadsheet formula injection), and
the three views are in geo_restriction.RESTRICTED_EXPORT_VIEWS by these names.
"""
from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from src.models import ActivityLog, ParliamentUser, ServicePeriod
from src.models.users import Role
from src.utils.export_utils import safe_csv_writer
from src.view.admin_v2 import require_admin_v2_auth

STATUSES = ('Active', 'Inactive', 'Alumni')


def _csv_response(request, name, kind):
    stamp = timezone.localdate().isoformat()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="parliament_{name}_{stamp}.csv"'
    ActivityLog.log_activity(action_type='other', user=request.user,
                             description=f'{request.user.get_display_name()} exported {kind}', request=request)
    return response


def _members(status):
    qs = ParliamentUser.objects.exclude(member_status='Removed')
    if status in STATUSES:
        qs = qs.filter(member_status=status)
    return qs.order_by('name')


@require_admin_v2_auth
def admin_v2_exports(request):
    periods = ServicePeriod.objects.order_by('-start_date')[:8]
    return render(request, 'admin_v2/exports.html', {'statuses': STATUSES, 'periods': periods})


@require_admin_v2_auth
def admin_v2_export_members(request):
    from django_otp.plugins.otp_totp.models import TOTPDevice

    status = request.GET.get('status', 'Active')
    members = list(_members(status).prefetch_related(
        Prefetch('roles', queryset=Role.objects.order_by('name')), 'committees'))
    totp = set(TOTPDevice.objects.filter(confirmed=True).values_list('user_id', flat=True))
    app = set(ParliamentUser.objects.filter(mobile_devices__revoked_at__isnull=True)
              .values_list('pk', flat=True))
    response = _csv_response(request, f'members_{status.lower() or "all"}', f'the member roster ({status or "all"})')
    w = safe_csv_writer(response)
    w.writerow(['Number', 'Name', 'Preferred name', 'Username', 'Email', 'Phone', 'Type', 'Status',
                'Pledge class', 'Roles', 'Committees', 'Admin', '2FA', 'App', 'Last seen'])
    for m in members:
        w.writerow([
            m.user_id, m.name, m.preferred_name, m.username, m.email, m.phone_number, m.member_type,
            m.member_status, m.pledge_class, '; '.join(r.name for r in m.roles.all()),
            '; '.join(c.name for c in m.committees.all()), 'yes' if m.is_admin else '',
            'yes' if m.pk in totp else '', 'yes' if m.pk in app else '',
            timezone.localtime(m.last_seen_at).strftime('%Y-%m-%d %H:%M') if m.last_seen_at else '',
        ])
    return response


@require_admin_v2_auth
def admin_v2_export_attendance(request):
    """Per member: required events in the window and how they went (same rate as My attendance)."""
    try:
        days = max(1, min(int(request.GET.get('days', 90)), 730))
    except ValueError:
        days = 90
    status = request.GET.get('status', 'Active')
    now = timezone.now()
    window = Q(attendance_records__attendance_type='event', attendance_records__event__requires_attendance=True,
               attendance_records__event__is_active=True, attendance_records__event__date_time__lt=now,
               attendance_records__event__date_time__gte=now - timedelta(days=days))
    rows = _members(status).annotate(
        total=Count('attendance_records', filter=window),
        present=Count('attendance_records', filter=window & Q(attendance_records__status='present')),
        late=Count('attendance_records', filter=window & Q(attendance_records__status='late')),
        absent=Count('attendance_records', filter=window & Q(attendance_records__status='absent')),
        excused=Count('attendance_records', filter=window & Q(attendance_records__status='excused')),
    )
    response = _csv_response(request, f'attendance_{days}d', f'attendance for the last {days} days')
    w = safe_csv_writer(response)
    w.writerow(['Number', 'Name', 'Type', 'Status', 'Events', 'Present', 'Late', 'Absent', 'Excused', 'Rate %'])
    for m in rows:
        rate = round((m.present + m.late) / m.total * 100) if m.total else ''
        w.writerow([m.user_id, m.get_display_name(), m.member_type, m.member_status,
                    m.total, m.present, m.late, m.absent, m.excused, rate])
    return response


@require_admin_v2_auth
def admin_v2_export_service(request):
    """Per member, for one service period: expected, approved, pending, remaining."""
    from src.view.service_user_dashboard import get_user_service_stats

    period = ServicePeriod.objects.filter(pk=request.GET.get('period') or 0).first() \
        or ServicePeriod.objects.filter(is_active=True).order_by('-start_date').first()
    response = _csv_response(request, 'service_hours', f'service hours ({period.name if period else "no period"})')
    w = safe_csv_writer(response)
    w.writerow(['Number', 'Name', 'Period', 'Expected', 'Approved', 'Pending', 'Remaining', 'Progress %'])
    if period is None:
        return response
    for m in _members(request.GET.get('status', 'Active')):
        s = get_user_service_stats(m, period)
        w.writerow([m.user_id, m.get_display_name(), period.name, s['expected_hours'], s['approved_hours'],
                    s['pending_hours'], s['remaining_hours'], s['progress_percent']])
    return response

