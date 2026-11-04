"""
Admin v2: chapter health (v4.4.4).

One read-only page that answers "who is slipping?" from data the site already
keeps. Five sections, each a short list linking to the member's admin page:

* Inactive members: nobody has seen them in 30 / 60 / 90 days (`?days=`), and
  the members who have never signed in at all. "Seen" is the newest of
  `last_seen_at` (any signed-in web request, SessionTrackingMiddleware),
  `last_login` (a password typed) and the newest `MobileDevice.last_seen_at`
  (the app), so a member on a long "remember me" session or the app only is
  not called inactive.
* Attendance this semester: the chapter's weekly rate and the members with the
  lowest rate. Same events and the same rate as Chapter Stats
  (src/view/officer/chapter_stats.py): finalized, active, attendance-required
  events, recruitment events left out; rate = (present + late) / records.
* No app: members with no live MobileDevice (not revoked, refresh window open;
  the same "live" as the Mobile app page).
* Pledges falling behind: live, unlocked tasks past their due date that the
  pledge has not completed or been waived from (PledgeTask.is_overdue_for and
  src/pledge_phases.py, as on the education dashboard), with required-task
  progress beside it.
* Service hours: members below the current period's requirement, with the
  same arithmetic as the service-hours CSV (approved + adjustments vs the
  member's expectation or the period default).

WHO COUNTS: Active members with an open account (`member_status` filters leave
closed accounts out, src/models/users.py), not the App Review demo account,
not Advisors. Pledges are out of the attendance list as on Chapter Stats, and
in the service list as on the service dashboard (pledges owe hours).

COST: a fixed number of queries whatever the roster size (a test holds it
flat). The numbers are cached for CACHE_SECONDS per `days` choice; System
tools' "Admin reports" scope drops them early.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import TruncWeek
from django.shortcuts import render
from django.utils import timezone

from src.app_review import exclude_review_accounts
from src.models import Attendance, Event, ParliamentUser, ServicePeriod
from src.models.education import PledgeTask, PledgeTaskCompletion
from src.models.service import ServiceHoursAdjustment, ServiceHoursSubmission
from src.pledge_phases import task_unlocked
from src.view.admin_v2 import require_admin_v2_auth

DAY_OPTIONS = (30, 60, 90)
LIST_LIMIT = 50
LOWEST_ATTENDANCE = 15
CACHE_SECONDS = 120
CACHE_PREFIX = 'admin_v2_health:v1:'


def cache_keys():
    """Every key this page writes (System tools clears them)."""
    return [f'{CACHE_PREFIX}{d}' for d in DAY_OPTIONS]


def clean_days(raw):
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DAY_OPTIONS[0]
    return days if days in DAY_OPTIONS else DAY_OPTIONS[0]


def semester_start(today=None):
    """Jan 1 for Spring, Jun 1 for Fall (src/utils/semester.py: no Summer term)."""
    today = today or timezone.localdate()
    return date(today.year, 1, 1) if today.month <= 5 else date(today.year, 6, 1)


def _aware(d):
    return timezone.make_aware(datetime.combine(d, time.min), timezone.get_current_timezone())


def _row(m, **extra):
    return {'user_id': m.user_id, 'name': m.get_display_name(), 'member_type': m.member_type, **extra}


def _members(now):
    """The roster, one query, with the app annotations every section needs."""
    live = Q(mobile_devices__revoked_at__isnull=True, mobile_devices__refresh_expires_at__gt=now)
    qs = (ParliamentUser.objects.filter(member_status='Active', is_active=True)
          .exclude(member_type='Advisor')
          .only('user_id', 'name', 'preferred_name', 'member_type', 'pledge_phase',
                'last_login', 'last_seen_at')
          .annotate(app_seen=Max('mobile_devices__last_seen_at'),
                    live_devices=Count('mobile_devices', filter=live))
          .order_by('name'))
    return list(exclude_review_accounts(qs))


def _last_active(m):
    seen = [t for t in (m.last_seen_at, m.last_login, m.app_seen) if t]
    return max(seen) if seen else None


def inactive_section(members, days, now):
    cutoff = now - timedelta(days=days)
    never, idle = [], []
    for m in members:
        last = _last_active(m)
        if last is None:
            never.append(_row(m))
        elif last < cutoff:
            idle.append(_row(m, last=last, days=(now - last).days))
    idle.sort(key=lambda r: r['last'])
    return {'days': days, 'n': len(idle), 'members': idle[:LIST_LIMIT],
            'never_count': len(never), 'never': never[:LIST_LIMIT]}


def attendance_section(members, now):
    start = semester_start(timezone.localdate(now))
    meetings = (Event.objects.filter(requires_attendance=True, attendance_finalized=True, is_active=True,
                                     date_time__gte=_aware(start), date_time__lte=now)
                .exclude(recruitment_event__isnull=False))
    records = Attendance.objects.filter(event__in=meetings, attendance_type='event')
    attended = Q(status__in=('present', 'late'))
    tz = timezone.get_current_timezone()
    weekly = {}
    for r in (records.order_by().annotate(week=TruncWeek('event__date_time', tzinfo=tz)).values('week')
              .annotate(total=Count('pk'), attended=Count('pk', filter=attended),
                        events=Count('event', distinct=True))):
        week = r['week'].date() if hasattr(r['week'], 'date') else r['week']
        weekly[week] = r
    monday = start - timedelta(days=start.weekday())
    this_monday = timezone.localdate(now) - timedelta(days=timezone.localdate(now).weekday())
    weeks = []
    d = monday
    while d <= this_monday:
        r = weekly.get(d)
        rate = round(r['attended'] / r['total'] * 100) if r and r['total'] else None
        weeks.append({'week': d, 'rate': rate, 'events': r['events'] if r else 0})
        d += timedelta(weeks=1)
    total = sum(r['total'] for r in weekly.values())
    overall = round(sum(r['attended'] for r in weekly.values()) / total * 100) if total else None

    eligible = {m.pk: m for m in members if m.member_type != 'Pledge'}
    per_member = (records.filter(user_id__in=list(eligible)).order_by().values('user')
                  .annotate(total=Count('pk'), attended=Count('pk', filter=attended)))
    lowest = []
    for r in per_member:
        m = eligible.get(r['user'])
        if m and r['total']:
            rate = round(r['attended'] / r['total'] * 100)
            lowest.append(_row(m, rate=rate, attended=r['attended'], total=r['total']))
    lowest.sort(key=lambda r: (r['rate'], -r['total'], r['name']))
    return {'start': start, 'weeks': weeks, 'overall': overall,
            'meetings': sum(w['events'] for w in weeks), 'lowest': lowest[:LOWEST_ATTENDANCE]}


def app_section(members):
    missing = [_row(m) for m in members if not m.live_devices]
    total = len(members)
    with_app = round((total - len(missing)) / total * 100) if total else 0
    return {'n': len(missing), 'total': total, 'with_app_percent': with_app,
            'members': missing[:LIST_LIMIT]}


def pledge_section(members, now):
    pledges = {m.pk: m for m in members if m.member_type == 'Pledge'}
    if not pledges:
        return {'n': 0, 'pledges': 0, 'members': []}
    tasks = list(PledgeTask.objects.filter(is_active=True).only(
        'pk', 'title', 'phase', 'due_date', 'is_required', 'is_active', 'activation_mode',
        'is_published', 'activates_at'))
    tasks = [t for t in tasks if t.is_live]
    task_ids = [t.pk for t in tasks]
    assigned = {}
    for task_id, user_id in (PledgeTask.assigned_to.through.objects.filter(pledgetask_id__in=task_ids)
                             .values_list('pledgetask_id', 'parliamentuser_id')):
        assigned.setdefault(task_id, set()).add(user_id)
    done = {(t, p): s for t, p, s in PledgeTaskCompletion.objects.filter(
        task_id__in=task_ids, pledge_id__in=list(pledges)).values_list('task_id', 'pledge_id', 'status')}
    today = timezone.localdate(now)
    behind = []
    for pk, m in pledges.items():
        overdue, required, completed = [], 0, 0
        for t in tasks:
            if assigned.get(t.pk) and pk not in assigned[t.pk]:
                continue
            if not task_unlocked(t.phase, m.pledge_phase):
                continue
            status = done.get((t.pk, pk))
            if t.is_required:
                required += 1
                completed += status == 'completed'
            if t.due_date and t.due_date < today and status not in PledgeTask.DONE_STATUSES:
                overdue.append(t.title)
        if overdue:
            behind.append(_row(m, overdue=len(overdue), titles=overdue[:3], completed=completed,
                               required=required,
                               percent=round(completed / required * 100) if required else 0))
    behind.sort(key=lambda r: (-r['overdue'], r['percent'], r['name']))
    return {'n': len(behind), 'pledges': len(pledges), 'members': behind[:LIST_LIMIT]}


def service_section(members, now):
    period = ServicePeriod.objects.filter(is_active=True).order_by('-start_date').first()
    if period is None:
        return {'period': None}
    approved, adjusted = {}, {}
    for row in (ServiceHoursSubmission.objects.filter(period=period, status='approved').order_by()
                .values('submitted_by_id').annotate(total=Sum('hours'))):
        approved[row['submitted_by_id']] = row['total'] or Decimal('0')
    for row in (ServiceHoursAdjustment.objects.filter(period=period).order_by()
                .values('member_id').annotate(total=Sum('hours'))):
        adjusted[row['member_id']] = row['total'] or Decimal('0')
    expected_map = dict(period.member_expectations.values_list('member_id', 'expected_hours'))
    below = []
    for m in members:
        expected = expected_map.get(m.pk, period.default_hours_required)
        if not expected or expected <= 0:
            continue
        hours = approved.get(m.pk, Decimal('0')) + adjusted.get(m.pk, Decimal('0'))
        if hours < expected:
            below.append(_row(m, hours=hours, expected=expected,
                              percent=max(0, min(100, int(hours / expected * 100)))))
    below.sort(key=lambda r: (r['percent'], r['name']))
    today = timezone.localdate(now)
    span = (period.end_date - period.start_date).days or 1
    elapsed = max(0, min(100, round((today - period.start_date).days / span * 100)))
    return {'period': {'name': period.name, 'end_date': period.end_date,
                       'default': period.default_hours_required},
            'elapsed': elapsed, 'n': len(below), 'members': below[:LIST_LIMIT]}


def chapter_health(days, now=None):
    now = now or timezone.now()
    members = _members(now)
    return {
        'computed_at': now,
        'member_count': len(members),
        'inactive': inactive_section(members, days, now),
        'attendance': attendance_section(members, now),
        'app': app_section(members),
        'pledges': pledge_section(members, now),
        'service': service_section(members, now),
    }


def cached_chapter_health(days):
    key = f'{CACHE_PREFIX}{days}'
    data = cache.get(key)
    if data is None:
        data = chapter_health(days)
        cache.set(key, data, CACHE_SECONDS)
    return data


@require_admin_v2_auth
def admin_v2_health(request):
    days = clean_days(request.GET.get('days'))
    return render(request, 'admin_v2/chapter_health.html', {
        'health': cached_chapter_health(days), 'days': days, 'day_options': DAY_OPTIONS,
        'cache_seconds': CACHE_SECONDS,
    })
