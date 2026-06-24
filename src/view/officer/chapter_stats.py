"""
Chapter statistics dashboard — officer-facing read-only aggregation view.

Aggregates attendance rates, voting participation, service hours completion,
and recruitment pipeline conversion. All data comes from existing models;
no new DB structures are required.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Count, Q, Sum
from datetime import timedelta

from src.decorators import officer_required
from src.models import (
    Event, Attendance, ParliamentUser,
    ServiceHoursSubmission, ServicePeriod,
)


STATS_DAY_OPTIONS = [30, 90, 180, 365]


@login_required
@officer_required
def chapter_stats(request):
    now = timezone.now()
    one_year_ago = now - timedelta(days=365)

    # ── Date-range param ───────────────────────────────────────────────────
    try:
        days = int(request.GET.get('days', 90))
    except (ValueError, TypeError):
        days = 90
    if days not in STATS_DAY_OPTIONS:
        days = 90
    cutoff = now - timedelta(days=days)
    period_label = f'{days} days'

    # ── Active member count ────────────────────────────────────────────────
    # Evaluate once, sorted by name, so both the attendance loop (needs PKs)
    # and the service hours breakdown loop (needs ordered names) share the same list.
    active_members = list(
        ParliamentUser.objects.filter(
            member_status='Active', is_active=True
        ).exclude(member_type__in=['Advisor', 'Pledge'])
        .order_by('name')
    )
    active_count = len(active_members)

    # ── Attendance ─────────────────────────────────────────────────────────
    # Chapter meetings (requires_attendance=True) in the last 90 days.
    # Exclude events linked to a RecruitmentEvent — those are recruitment-only
    # and should not pollute chapter meeting attendance stats.
    chapter_meetings = Event.objects.filter(
        requires_attendance=True,
        attendance_finalized=True,
        date_time__gte=cutoff,
        date_time__lte=now,
        is_active=True,
    ).exclude(
        recruitment_event__isnull=False
    ).order_by('-date_time')
    meeting_count = chapter_meetings.count()

    # Overall attendance rate across those meetings — single aggregated query
    if meeting_count > 0:
        agg = Attendance.objects.filter(
            event__in=chapter_meetings,
            attendance_type='event',
        ).aggregate(
            total=Count('id'),
            present=Count('id', filter=Q(status__in=['present', 'late'])),
            excused=Count('id', filter=Q(status='excused')),
        )
        total_records = agg['total'] or 0
        present_records = agg['present'] or 0
        excused_records = agg['excused'] or 0
        attendance_rate = round(present_records / total_records * 100, 1) if total_records else 0
    else:
        total_records = present_records = excused_records = 0
        attendance_rate = None

    # Per-meeting breakdown (last 10 finalized meetings) — single aggregated query
    recent_meetings = list(chapter_meetings[:10])
    breakdown_qs = (
        Attendance.objects
        .filter(event__in=recent_meetings, attendance_type='event')
        .values('event')
        .annotate(
            total=Count('id'),
            present=Count('id', filter=Q(status__in=['present', 'late'])),
            excused=Count('id', filter=Q(status='excused')),
            absent=Count('id', filter=Q(status='absent')),
        )
    )
    breakdown_by_event = {row['event']: row for row in breakdown_qs}
    meeting_breakdown = []
    for mtg in recent_meetings:
        row = breakdown_by_event.get(mtg.pk, {})
        total = row.get('total', 0)
        present = row.get('present', 0)
        meeting_breakdown.append({
            'event': mtg,
            'total': total,
            'present': present,
            'excused': row.get('excused', 0),
            'absent': row.get('absent', 0),
            'rate': round(present / total * 100, 1) if total else 0,
        })

    # Members with below-75% attendance in last 90 days — single aggregated query
    at_risk_attendance = []
    if meeting_count >= 3:
        member_stats = (
            Attendance.objects
            .filter(event__in=chapter_meetings, attendance_type='event', user__in=active_members)
            .values('user')
            .annotate(
                total=Count('id'),
                attended=Count('id', filter=Q(status__in=['present', 'late', 'excused'])),
            )
        )
        stats_by_user = {s['user']: s for s in member_stats}
        # Pull member objects for at-risk users only
        at_risk_pks = [
            pk for pk, s in stats_by_user.items()
            if s['total'] > 0 and (s['attended'] / s['total']) < 0.75
        ]
        at_risk_set = set(at_risk_pks)
        members_by_pk = {m.pk: m for m in active_members if m.pk in at_risk_set}
        for pk in at_risk_pks:
            s = stats_by_user[pk]
            member = members_by_pk.get(pk)
            if member:
                at_risk_attendance.append({
                    'member': member,
                    'present': s['attended'],
                    'total': s['total'],
                    'rate': round(s['attended'] / s['total'] * 100, 1),
                })
        at_risk_attendance.sort(key=lambda x: x['rate'])

    # ── Voting participation ────────────────────────────────────────────────
    from src.models import Legislation, Vote
    recent_legislation = Legislation.objects.filter(
        created_at__gte=cutoff,
        voting_closed=True,
    ).order_by('-created_at')
    legislation_count = recent_legislation.count()

    recent_legs = list(recent_legislation[:10])
    vote_breakdown = []
    total_possible_votes = 0
    total_cast_votes = 0
    if recent_legs:
        vote_counts = (
            Vote.objects
            .filter(legislation__in=recent_legs)
            .values('legislation')
            .annotate(cast=Count('user', distinct=True))
        )
        cast_by_leg = {row['legislation']: row['cast'] for row in vote_counts}
        for leg in recent_legs:
            cast = cast_by_leg.get(leg.pk, 0)
            possible = active_count
            rate = round(cast / possible * 100, 1) if possible else 0
            total_cast_votes += cast
            total_possible_votes += possible
            vote_breakdown.append({
                'legislation': leg,
                'cast': cast,
                'possible': possible,
                'rate': rate,
            })
    overall_vote_rate = (
        round(total_cast_votes / total_possible_votes * 100, 1)
        if total_possible_votes else None
    )

    # ── Service hours ──────────────────────────────────────────────────────
    active_period = ServicePeriod.objects.filter(is_active=True).order_by('-start_date').first()
    service_stats = None
    service_breakdown = []
    if active_period:
        default_required = float(active_period.default_hours_required or 0)

        # Fetch per-member overrides in one query so we don't hit the DB per member
        overrides_by_member = {
            o.member_id: float(o.expected_hours)
            for o in active_period.member_expectations.all()
        }

        submissions = ServiceHoursSubmission.objects.filter(
            period=active_period, status='approved'
        ).values('submitted_by').annotate(total=Sum('hours'))

        completed_by_pk = {s['submitted_by']: float(s['total']) for s in submissions}

        # Count members who met *their individual* requirement
        met_requirement = 0
        for member in active_members:
            member_required = overrides_by_member.get(member.pk, default_required)
            hours = completed_by_pk.get(member.pk, 0)
            if member_required and hours >= member_required:
                met_requirement += 1

        service_stats = {
            'period': active_period,
            'required_hours': default_required,
            'members_met': met_requirement,
            'members_total': active_count,
            'completion_rate': round(met_requirement / active_count * 100, 1) if active_count else 0,
        }

        # Per-member breakdown — show their individual requirement and whether they met it
        # active_members is already a sorted list; no second DB query needed.
        for member in active_members:
            hours = completed_by_pk.get(member.pk, 0)
            member_required = overrides_by_member.get(member.pk, default_required)
            service_breakdown.append({
                'member': member,
                'hours': hours,
                'required': member_required,
                'has_override': member.pk in overrides_by_member,
                'met': bool(member_required and hours >= member_required),
            })
        service_breakdown.sort(key=lambda x: -x['hours'])

    # ── Recruitment pipeline ───────────────────────────────────────────────
    recruitment_stats = None
    try:
        from src.models import RecruitmentEvent, RecruitmentEventRSVP
        events_this_year = RecruitmentEvent.objects.filter(
            event__date_time__gte=one_year_ago
        )
        total_rsvps = RecruitmentEventRSVP.objects.filter(
            recruitment_event__in=events_this_year, status='going'
        ).values('user').distinct().count()
        recruitment_event_count = events_this_year.count()
        recruitment_stats = {
            'event_count': recruitment_event_count,
            'unique_rsvps': total_rsvps,
            'pledges_initiated': ParliamentUser.objects.filter(member_type='Pledge', is_active=True).count(),
        }
    except Exception:
        pass  # Recruitment module may not be set up yet

    context = {
        'active_count': active_count,
        # Attendance
        'meeting_count': meeting_count,
        'attendance_rate': attendance_rate,
        'present_records': present_records,
        'excused_records': excused_records,
        'total_records': total_records,
        'meeting_breakdown': meeting_breakdown,
        'at_risk_attendance': at_risk_attendance[:10],
        # Voting
        'legislation_count': legislation_count,
        'overall_vote_rate': overall_vote_rate,
        'vote_breakdown': vote_breakdown,
        # Service hours
        'service_stats': service_stats,
        'service_breakdown': service_breakdown[:20],
        # Recruitment
        'recruitment_stats': recruitment_stats,
        # Date-range picker
        'days': days,
        'day_options': STATS_DAY_OPTIONS,
        'period_label': period_label,
    }
    return render(request, 'officer/chapter_stats.html', context)
