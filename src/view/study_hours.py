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
    return ParliamentUser.objects.filter(member_status='Active').order_by('name')


@login_required
def event_time(request, event_id):
    event = get_object_or_404(Event, pk=event_id)
    if not lt.is_timed(event) or not lt.can_adjust(request.user):
        raise Http404
    if request.method == 'POST':
        member = ParliamentUser.objects.filter(pk=request.POST.get('user_id')).first()
        try:
            delta = int(request.POST.get('minutes') or 0)
            if request.POST.get('direction') == 'remove':
                delta = -abs(delta)
            if member is None:
                raise ValueError('Choose a member.')
            total = lt.adjust(request, member, event, delta, request.POST.get('reason'), request.user)
        except ValueError as e:
            messages.error(request, str(e))
        else:
            messages.success(request, f'{member.get_display_name()} now has {total} min.')
        return redirect('event_time', event_id=event.id)

    opens, closes = lt.window(event)
    rows = [{
        'member': m,
        'minutes': got,
        'met': bool(event.checkin_min_minutes) and got >= event.checkin_min_minutes,
        'visits': visits,
        'adjustments': adjustments,
        'unconfirmed': any(v.left_unconfirmed for v in visits),
        'inside': any(v.left_at is None for v in visits),
    } for m, got, visits, adjustments in lt.members_with_time(event)]
    listed = {r['member'].pk for r in rows}
    return render(request, 'officer/event_time.html', {
        'event': event,
        'opens': opens,
        'closes': closes,
        'rows': rows,
        'others': [m for m in _active_members() if m.pk not in listed],
    })


def _week(request):
    day = parse_date(request.GET.get('week') or '')
    start = lt.week_start(timezone.make_aware(datetime.combine(day, time(12))) if day else None)
    return start, start + timedelta(days=7)


@login_required
def education_study_hours(request, code):
    committee, access = _education_committee_or_404(code, request.user)
    if not (access['can_view_submissions'] or access['can_adjust_time']):
        raise Http404
    start, end = _week(request)

    if request.method == 'POST':
        if not access['can_adjust_time']:
            raise Http404
        member = get_object_or_404(ParliamentUser, pk=request.POST.get('user_id'))
        raw = (request.POST.get('hours') or '').strip()
        if not raw:
            StudyHoursRequirement.objects.filter(user=member).delete()
            messages.success(request, f'{member.get_display_name()} has no weekly requirement now.')
        else:
            try:
                hours = float(raw)
                if not 0 < hours <= 80:
                    raise ValueError
            except ValueError:
                messages.error(request, 'Hours per week must be a number from 0.5 to 80.')
            else:
                StudyHoursRequirement.objects.update_or_create(
                    user=member, defaults={'weekly_minutes': round(hours * 60), 'set_by': request.user})
                messages.success(request, f'{member.get_display_name()}: {hours:g} hours a week.')
        return redirect(f"{request.path}?week={start.date().isoformat()}")

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
    return render(request, 'committee/education_study_hours.html', {
        'committee': committee,
        'access': access,
        'week_start': start,
        'week_end': end - timedelta(days=1),
        'prev_week': (start - timedelta(days=7)).date().isoformat(),
        'next_week': (start + timedelta(days=7)).date().isoformat(),
        'rows': rows,
        'events': events,
        'can_adjust': lt.can_adjust(request.user),
        'without_requirement': [m for m in members if m.pk not in requirements],
    })
