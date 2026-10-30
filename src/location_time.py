"""
Time-based location check-in (10-30-26): the VP of Education's request.

Two kinds of event count the time a member's phone spends inside the event's
check-in area (``src.location_checkin`` has the area, the distance and
accuracy rules, and the check-in key):

* **Minimum stay** (``Event.checkin_min_minutes``): an attendance event where
  arriving isn't enough. The member is marked present once their time inside
  (all visits added up, so stepping out briefly is fine) reaches the minimum.
  Window: the usual check-in window (an hour before until three hours after).
* **Study hours** (``Event.is_study_hours``): time inside from the event's
  start for ``duration_minutes`` counts toward the member's study hours. No attendance.
  Pledges can be given a weekly requirement (``StudyHoursRequirement``),
  shown on their education dashboard.

The app reports arriving (a position, checked here like any location
check-in) and leaving (``record_leave``), from iOS geofence events, which
need "Always" location access; with "While using" it reports whatever it
sees each time the app is opened. A visit whose "left" never arrives (phone
off, no signal) stops counting when the window closes and is marked
``left_unconfirmed`` so officers can check it. Officers, and whoever the VP
of Education allows, can add or remove minutes with a reason
(``LocationTimeAdjustment``).

Credit is checked when the member arrives or leaves, when time is adjusted,
and every couple of minutes by ``tick`` (Celery), so a member who reaches the
minimum while still inside is marked present without leaving.
"""
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from src import location_checkin as lc

#: Longest study hours event (a typo'd end time shouldn't count days).
MAX_STUDY_SPAN = timedelta(hours=16)
MAX_ADJUST_MINUTES = 12 * 60


def is_timed(event):
    return bool(event.is_study_hours or event.checkin_min_minutes)


def window(event):
    """(opens, closes) during which time inside counts."""
    if event.is_study_hours:
        length = timedelta(minutes=event.duration_minutes or 120)
        return event.date_time, event.date_time + min(length, MAX_STUDY_SPAN)
    return lc.window(event)


def _clip(visit, opens, closes, now):
    start = max(visit.entered_at, opens)
    end = min(visit.left_at or now, closes, now)
    return max(0.0, (end - start).total_seconds())


def minutes(user, event, now=None, visits=None, adjustments=None):
    """Whole minutes ``user`` has at ``event``: visits inside the window plus adjustments."""
    from src.models import LocationTimeAdjustment, LocationVisit
    now = now or timezone.now()
    opens, closes = window(event)
    if visits is None:
        visits = LocationVisit.objects.filter(event=event, user=user)
    seconds = sum(_clip(v, opens, closes, now) for v in visits)
    if adjustments is None:
        adjustments = (LocationTimeAdjustment.objects.filter(event=event, user=user)
                       .aggregate(total=Sum('minutes'))['total'] or 0)
    return max(0, int(seconds // 60) + adjustments)


def open_visit(user, event):
    from src.models import LocationVisit
    return LocationVisit.objects.filter(event=event, user=user, left_at__isnull=True).first()


def _refusal(user, event, now):
    """The location_checkin refusals that apply to a timed event (no distance check)."""
    if not lc.has_area(event):
        return 'no_area'
    if not event.is_study_hours and event.attendance_finalized:
        return 'finalized'
    if user.member_status != 'Active':
        return 'not_eligible'
    opens, closes = window(event)
    if not opens <= now <= closes:
        return 'not_open'
    return None


def record_enter(request, user, event, lat, lng, accuracy, now=None):
    """
    The phone is inside (or says so): start a visit if none is open, then check
    credit. Returns ``(result, minutes, distance)``; result is ``'success'`` or a
    key of ``location_checkin.REFUSALS``.
    """
    import math
    from src.models import LocationVisit
    now = now or timezone.now()
    refusal = _refusal(user, event, now)
    if refusal:
        return refusal, None, None
    if accuracy is None or not math.isfinite(accuracy) or accuracy < 0 or accuracy > lc.MAX_ACCURACY_M:
        return 'inaccurate', None, None
    distance = lc.distance_m(lat, lng, event.checkin_latitude, event.checkin_longitude)
    if distance - min(accuracy, lc.ACCURACY_ALLOWANCE_M) > event.checkin_radius_m:
        return 'too_far', None, round(distance)
    with transaction.atomic():
        if not LocationVisit.objects.select_for_update().filter(
                event=event, user=user, left_at__isnull=True).exists():
            LocationVisit.objects.create(event=event, user=user, entered_at=now)
    credit(request, user, event, now)
    return 'success', minutes(user, event, now), round(distance)


def record_leave(request, user, event, now=None):
    """The phone left the area: close the open visit (if any) and check credit. Returns minutes."""
    now = now or timezone.now()
    visit = open_visit(user, event)
    if visit:
        _, closes = window(event)
        visit.left_at = max(visit.entered_at, min(now, closes))
        visit.save(update_fields=['left_at'])
        credit(request, user, event, now)
    return minutes(user, event, now)


def credit(request, user, event, now=None):
    """
    Minimum-stay events: mark ``user`` present once their minutes reach the
    minimum. Same rule as a location check-in: only a missing or pending row,
    so an officer's marking stays as it was. Returns True if marked now.
    """
    from src.models import ActivityLog, Attendance
    if event.is_study_hours or not event.checkin_min_minutes or event.attendance_finalized:
        return False
    now = now or timezone.now()
    got = minutes(user, event, now)
    if got < event.checkin_min_minutes:
        return False
    existing = Attendance.objects.filter(event=event, user=user, attendance_type='event').first()
    if existing and existing.status != 'pending':
        return False
    Attendance.objects.update_or_create(
        event=event, user=user, attendance_type='event',
        defaults={
            'status': 'present',
            'marked_by': user,
            'marked_at': now,
            'notes': f'Checked in by location: {got} min at the event (needed {event.checkin_min_minutes})',
        },
    )
    ActivityLog.log_activity(
        action_type='attendance_taken', user=user,
        description=f'{user.name} checked in to {event.title} by location after {got} min',
        request=request, object_type='Event', object_id=event.id, object_repr=str(event),
    )
    return True


def tick(now=None):
    """
    Every couple of minutes (Celery): credit members still inside minimum-stay
    events, and close visits whose window has ended without a "left" (marked
    unconfirmed). Returns (credited, closed).
    """
    from src.models import LocationVisit
    now = now or timezone.now()
    credited = closed = 0
    for visit in (LocationVisit.objects.filter(left_at__isnull=True)
                  .select_related('event', 'user')):
        event = visit.event
        _, closes = window(event)
        if now >= closes:
            visit.left_at = max(visit.entered_at, closes)
            visit.left_unconfirmed = True
            visit.save(update_fields=['left_at', 'left_unconfirmed'])
            closed += 1
        if credit(None, visit.user, event, now):
            credited += 1
    return credited, closed


def can_adjust(user):
    """Officers and chairs, education committee chairs, and members granted ``can_adjust_time``."""
    from src.models import Committee, EducationMemberPermission
    from src.permissions import user_is_officer_or_chair
    if user_is_officer_or_chair(user) or user.is_admin:
        return True
    if Committee.objects.filter(is_education_committee=True, chairs=user).exists():
        return True
    return EducationMemberPermission.objects.filter(user=user, can_adjust_time=True).exists()


def adjust(request, member, event, delta, reason, by):
    """Add (or with a negative ``delta``, remove) minutes; logged. Returns the new total."""
    from src.models import ActivityLog, LocationTimeAdjustment
    delta = int(delta)
    reason = (reason or '').strip()[:300]
    if not delta or abs(delta) > MAX_ADJUST_MINUTES:
        raise ValueError(f'Adjust by between 1 and {MAX_ADJUST_MINUTES} minutes.')
    if not reason:
        raise ValueError('Give a reason for the change.')
    LocationTimeAdjustment.objects.create(event=event, user=member, minutes=delta, reason=reason, adjusted_by=by)
    ActivityLog.log_activity(
        action_type='other', user=by,
        description=f'{"Added" if delta > 0 else "Removed"} {abs(delta)} min for {member.name} at {event.title}: {reason}',
        request=request, object_type='Event', object_id=event.id, object_repr=str(event),
    )
    credit(request, member, event)
    return minutes(member, event)


# ── Study hours ─────────────────────────────────────────────────────────────

def week_start(day=None):
    """Monday 00:00 (local time) of the week containing ``day``."""
    day = timezone.localtime(day or timezone.now()).date()
    monday = day - timedelta(days=day.weekday())
    return timezone.make_aware(datetime.combine(monday, time.min))


def study_minutes(users, start, end, now=None):
    """{user_id: minutes} at study hours events between ``start`` and ``end`` (event start times)."""
    from src.models import Event, LocationTimeAdjustment, LocationVisit
    now = now or timezone.now()
    events = {e.id: e for e in Event.objects.filter(is_study_hours=True, date_time__gte=start, date_time__lt=end)}
    user_ids = [getattr(u, 'pk', u) for u in users]
    out = {uid: 0.0 for uid in user_ids}
    for v in LocationVisit.objects.filter(event_id__in=events, user_id__in=user_ids):
        opens, closes = window(events[v.event_id])
        out[v.user_id] += _clip(v, opens, closes, now) / 60
    for a in LocationTimeAdjustment.objects.filter(event_id__in=events, user_id__in=user_ids):
        out[a.user_id] += a.minutes
    return {uid: max(0, int(m)) for uid, m in out.items()}


def study_week(user, now=None):
    """This week's study hours for ``user``: minutes, the requirement (or None), and each event."""
    from src.models import Event, StudyHoursRequirement
    now = now or timezone.now()
    start = week_start(now)
    end = start + timedelta(days=7)
    req = StudyHoursRequirement.objects.filter(user=user).first()
    events = Event.objects.filter(is_study_hours=True, date_time__gte=start, date_time__lt=end).order_by('date_time')
    return {
        'week_start': start,
        'minutes': study_minutes([user], start, end, now)[user.pk],
        'required_minutes': req.weekly_minutes if req else None,
        'events': [{'event': e, 'minutes': minutes(user, e, now)} for e in events],
    }


def visible_study_events(user, start, end):
    from src.models import Event
    from src.utils.visibility import visible_to_q
    return (Event.objects.filter(is_study_hours=True, is_active=True, date_time__gte=start, date_time__lt=end)
            .filter(visible_to_q(user.member_type)).order_by('date_time'))


def members_with_time(event):
    """[(member, minutes, visits, adjustments)] for everyone with a visit or adjustment at ``event``."""
    from src.models import LocationTimeAdjustment, LocationVisit, ParliamentUser
    visits, adjustments = {}, {}
    for v in LocationVisit.objects.filter(event=event).order_by('entered_at'):
        visits.setdefault(v.user_id, []).append(v)
    for a in LocationTimeAdjustment.objects.filter(event=event).select_related('adjusted_by').order_by('created_at'):
        adjustments.setdefault(a.user_id, []).append(a)
    ids = set(visits) | set(adjustments)
    now = timezone.now()
    rows = []
    for m in ParliamentUser.objects.filter(pk__in=ids).order_by('name'):
        adj = adjustments.get(m.pk, [])
        rows.append((m, minutes(m, event, now, visits=visits.get(m.pk, []), adjustments=sum(a.minutes for a in adj)),
                     visits.get(m.pk, []), adj))
    return rows

