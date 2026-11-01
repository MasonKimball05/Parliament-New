"""
Timed check-in and study hours on the website (10-30-26; rules in
``src/location_time.py``).

* ``event_time`` (``/officers/events/<id>/time/``): who has time at a timed
  event (a minimum stay, or study hours), each visit, and the adjustments;
  add or remove minutes for anyone, with a reason. For whoever
  ``location_time.can_adjust`` allows: officers and chairs, the education
  committee's chairs, and members they gave the "Time" permission.
* ``education_study_hours`` (``/committee/<code>/education/study-hours/``):
  the week's study hours per member against their weekly requirement, this
  week's study hours events, and setting requirements (``can_adjust_time``).
"""
from datetime import datetime, time, timedelta

from src.app_review import exclude_review_accounts
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date

from src import location_time as lt
from src.models import Event, ParliamentUser, StudyHoursRequirement
from src.view.committee.education import _education_committee_or_404


def _active_members():
    return exclude_review_accounts(ParliamentUser.objects.filter(member_status='Active')).order_by('name')


# ── Shared with the app (src/mobile/views_study_hours.py) ───────────────────

def timed_event_or_404(event_id, user):
    """The event, if it is timed and ``user`` may adjust time (``can_adjust``); else Http404."""
    event = get_object_or_404(Event, pk=event_id)
    if not lt.is_timed(event) or not lt.can_adjust(user):
        raise Http404
    return event


def event_time_rows(event):
    """Everyone with time at ``event``: minutes, visits, adjustments and the flags the page shows."""
    return [{
        'member': m,
        'minutes': got,
        'met': bool(event.checkin_min_minutes) and got >= event.checkin_min_minutes,
        'visits': visits,
        'adjustments': adjustments,
        'unconfirmed': any(v.left_unconfirmed for v in visits),
        'inside': any(v.left_at is None for v in visits),
    } for m, got, visits, adjustments in lt.members_with_time(event)]


def members_without_time(rows):
    """Active members not in ``rows``: the "Add time for someone else" choices."""
    listed = {r['member'].pk for r in rows}
    return [m for m in _active_members() if m.pk not in listed]


def adjust_time(request, event, user_id, minutes, direction, reason):
    """
    The page's add/remove form: ``minutes`` (a whole number), ``direction``
    'remove' to take them away. Returns ``(member, new total)``; raises
    ValueError with the page's message (``location_time.adjust`` checks the
    size and the reason, and logs it).
    """
    member = ParliamentUser.objects.filter(pk=user_id).first() if user_id not in (None, '') else None
    delta = int(minutes or 0)
    if direction == 'remove':
        delta = -abs(delta)
    if member is None:
        raise ValueError('Choose a member.')
    return member, lt.adjust(request, member, event, delta, reason, request.user)


def week_bounds(value):
    """``?week=`` (any day in it, YYYY-MM-DD; this week when blank or bad) → (Monday start, next Monday)."""
    day = parse_date(value or '') if value else None
    start = lt.week_start(timezone.make_aware(datetime.combine(day, time(12))) if day else None)
    return start, start + timedelta(days=7)


def set_requirement(member, raw, by):
    """
    Set ``member``'s weekly study hours from the page's "hours" field (blank
    clears it). Returns the hours set, or None when cleared; raises ValueError
    with the page's message for anything but 0.5–80.
    """
    raw = (str(raw) if raw is not None else '').strip()
    if not raw:
        StudyHoursRequirement.objects.filter(user=member).delete()
        return None
    try:
        hours = float(raw)
        if not 0 < hours <= 80:
            raise ValueError
    except ValueError:
        raise ValueError('Hours per week must be a number from 0.5 to 80.')
    StudyHoursRequirement.objects.update_or_create(
        user=member, defaults={'weekly_minutes': round(hours * 60), 'set_by': by})
    return hours


def study_hours_week(start, end):
    """The week's rows (members with time or a requirement), its study hours events, and who has no requirement."""
    requirements = {r.user_id: r.weekly_minutes for r in StudyHoursRequirement.objects.all()}
    members = list(_active_members())
    got = lt.study_minutes(members, start, end)
    rows = [{
        'member': m,
        'minutes': got.get(m.pk, 0),
        'required': requirements.get(m.pk),
        'percent': min(100, round(100 * got.get(m.pk, 0) / requirements[m.pk])) if requirements.get(m.pk) else None,
    } for m in members if got.get(m.pk) or m.pk in requirements]
    rows.sort(key=lambda r: (r['required'] is None, r['member'].name))
    events = Event.objects.filter(is_study_hours=True, date_time__gte=start, date_time__lt=end).order_by('date_time')
    return {
        'rows': rows,
        'events': events,
        'without_requirement': [m for m in members if m.pk not in requirements],
    }


# ── Pages ───────────────────────────────────────────────────────────────────

@login_required
def event_time(request, event_id):
    event = timed_event_or_404(event_id, request.user)
    if request.method == 'POST':
        try:
            member, total = adjust_time(request, event, request.POST.get('user_id'), request.POST.get('minutes'),
                                        request.POST.get('direction'), request.POST.get('reason'))
        except ValueError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f'{member.get_display_name()} now has {total} min.')
        return redirect('event_time', event_id=event.id)

    opens, closes = lt.window(event)
    rows = event_time_rows(event)
    return render(request, 'officer/event_time.html', {
        'event': event,
        'opens': opens,
        'closes': closes,
        'rows': rows,
        'others': members_without_time(rows),
    })


@login_required
def education_study_hours(request, code):
    committee, access = _education_committee_or_404(code, request.user)
    if not (access['can_view_submissions'] or access['can_adjust_time']):
        raise Http404
    start, end = week_bounds(request.GET.get('week'))

    if request.method == 'POST':
        if not access['can_adjust_time']:
            raise Http404
        member = get_object_or_404(ParliamentUser, pk=request.POST.get('user_id'))
        try:
            hours = set_requirement(member, request.POST.get('hours'), request.user)
        except ValueError as e:
            messages.error(request, str(e))
        else:
            if hours is None:
                messages.success(request, f'{member.get_display_name()} has no weekly requirement now.')
            else:
                messages.success(request, f'{member.get_display_name()}: {hours:g} hours a week.')
        return redirect(f"{request.path}?week={start.date().isoformat()}")

    week = study_hours_week(start, end)
    return render(request, 'committee/education_study_hours.html', {
        'committee': committee,
        'access': access,
        'week_start': start,
        'week_end': end - timedelta(days=1),
        'prev_week': (start - timedelta(days=7)).date().isoformat(),
        'next_week': (start + timedelta(days=7)).date().isoformat(),
        'rows': week['rows'],
        'events': week['events'],
        'can_adjust': lt.can_adjust(request.user),
        'without_requirement': week['without_requirement'],
    })
