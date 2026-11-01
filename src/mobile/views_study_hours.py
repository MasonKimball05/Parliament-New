"""
Study hours and time at timed events in the app (rules in
``src/location_time.py``), the native versions of the website's
``education_study_hours`` and ``event_time`` pages (``src/view/study_hours.py``,
whose helpers these call).

* ``GET committees/<code>/education/study-hours/?week=YYYY-MM-DD`` — the week
  (Monday to Sunday) holding that day, this week without one: each member with
  study time or a weekly requirement, their minutes against it, the week's
  study hours events, and (for whoever may set requirements) the members
  without one. Readable with the education permission to view submissions or
  to adjust time, as the page; anyone else gets a 404.
* ``POST committees/<code>/education/study-hours/requirements/``
  ``{user_id, hours}`` — set a member's weekly requirement in hours (0.5–80);
  a blank or null ``hours`` clears it. ``can_adjust_time`` only.
* ``GET officer/events/<id>/time/`` — who has time at a timed event (a
  minimum stay, or study hours): minutes, each visit, each adjustment, whether
  they're inside now or a visit never reported leaving; and the active members
  with no time yet, for "add time for someone else".
* ``POST officer/events/<id>/time/`` ``{user_id, minutes, direction, reason}``
  — add (``direction`` 'add') or remove ('remove') whole minutes with a reason
  (``location_time.adjust``: 1–720 minutes, logged to the activity log, and a
  minimum-stay event marks the member present once they reach it). Answers
  with ``adjusted`` and the event's time as GET returns it.

Both event-time routes are for whoever ``location_time.can_adjust`` allows, on
a timed event; anything else is a 404, as on the page.
"""
from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_date
from rest_framework import exceptions
from rest_framework.response import Response

from src import location_time as lt
from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _person
from src.mobile.views_excuses_polls import BadRequest
from src.models import ParliamentUser
from src.view.committee.education import _education_committee_or_404
from src.view.study_hours import (
    adjust_time, event_time_rows, members_without_time, set_requirement, study_hours_week,
    timed_event_or_404, week_bounds,
)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _member_json(m):
    return {**_person(m), 'member_type': m.member_type, 'is_pledge': m.member_type == 'Pledge'}


class _StudyHoursBase(MobileAPIView):
    web_page = 'education_study_hours'
    web_page_toggle = False   # education_study_hours has no @require_page_enabled

    def setup_access(self, request, code):
        self.committee, self.access = _education_committee_or_404(code, request.user)
        return self.committee


class StudyHoursView(_StudyHoursBase):

    def get(self, request, code):
        committee = self.setup_access(request, code)
        if not (self.access['can_view_submissions'] or self.access['can_adjust_time']):
            raise exceptions.NotFound()
        raw = (request.query_params.get('week') or '').strip()
        try:
            if raw and parse_date(raw) is None:
                raise ValueError
            start, end = week_bounds(raw)
        except ValueError:
            raise BadRequest('week must be a date (YYYY-MM-DD).', code='invalid_input')
        week = study_hours_week(start, end)
        can_set = bool(self.access['can_adjust_time'])
        can_adjust = lt.can_adjust(request.user)
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'week_start': start.date().isoformat(),
            'week_end': (end - timedelta(days=1)).date().isoformat(),
            'prev_week': (start - timedelta(days=7)).date().isoformat(),
            'next_week': (start + timedelta(days=7)).date().isoformat(),
            'rows': [{
                **_member_json(r['member']),
                'minutes': r['minutes'],
                'required_minutes': r['required'],
                'percent': r['percent'],
            } for r in week['rows']],
            'events': [{
                'id': e.id,
                'title': e.title,
                'date_time': e.date_time.isoformat(),
                'time_path': reverse('event_time', args=[e.id]) if can_adjust else None,
            } for e in week['events']],
            'can_set_requirements': can_set,
            'can_adjust': can_adjust,
            'without_requirement': [_member_json(m) for m in week['without_requirement']] if can_set else [],
            'web': {'page': reverse('education_study_hours', kwargs={'code': committee.code})},
        })


class StudyHoursRequirementView(_StudyHoursBase):

    def post(self, request, code):
        self.setup_access(request, code)
        if not self.access['can_adjust_time']:
            raise exceptions.PermissionDenied('You don’t have permission to do that.', code='forbidden')
        body = _body(request)
        member = get_object_or_404(ParliamentUser, pk=str(body.get('user_id') or ''))
        try:
            hours = set_requirement(member, body.get('hours'), request.user)
        except ValueError as e:
            raise BadRequest(str(e), code='invalid_input')
        return Response({
            'user_id': member.user_id,
            'required_minutes': round(hours * 60) if hours is not None else None,
        })


class EventTimeView(MobileAPIView):
    web_page = 'event_time'
    web_page_toggle = False   # event_time has no @require_page_enabled

    def _state(self, event):
        opens, closes = lt.window(event)
        rows = event_time_rows(event)
        return {
            'event': {
                'id': event.id,
                'title': event.title,
                'date_time': event.date_time.isoformat(),
                'is_study_hours': event.is_study_hours,
                'checkin_min_minutes': event.checkin_min_minutes,
            },
            'opens_at': opens.isoformat(),
            'closes_at': closes.isoformat(),
            'rows': [{
                **_person(r['member']),
                'role_number': r['member'].role_number or '',
                'minutes': r['minutes'],
                'met': r['met'],
                'inside': r['inside'],
                'unconfirmed': r['unconfirmed'],
                'visits': [{
                    'id': v.id,
                    'entered_at': v.entered_at.isoformat(),
                    'left_at': v.left_at.isoformat() if v.left_at else None,
                    'left_unconfirmed': v.left_unconfirmed,
                } for v in r['visits']],
                'adjustments': [{
                    'id': a.id,
                    'minutes': a.minutes,
                    'reason': a.reason,
                    'adjusted_by': a.adjusted_by.get_display_name() if a.adjusted_by_id else None,
                    'created_at': a.created_at.isoformat(),
                } for a in r['adjustments']],
            } for r in rows],
            'others': [_person(m) for m in members_without_time(rows)],
            'max_adjust_minutes': lt.MAX_ADJUST_MINUTES,
            'web': {'page': reverse('event_time', args=[event.id])},
        }

    def get(self, request, event_id):
        return Response(self._state(timed_event_or_404(event_id, request.user)))

    def post(self, request, event_id):
        event = timed_event_or_404(event_id, request.user)
        body = _body(request)
        direction = body.get('direction') or 'add'
        if direction not in ('add', 'remove'):
            raise BadRequest('direction is "add" or "remove".', code='invalid_input')
        raw = body.get('minutes')
        if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).strip().isdigit():
            raise BadRequest('Minutes must be a whole number (direction says add or remove).', code='invalid_input')
        user_id = body.get('user_id')
        try:
            member, total = adjust_time(request, event, str(user_id) if user_id not in (None, '') else None,
                                        int(str(raw).strip()), direction, body.get('reason'))
        except ValueError as e:
            raise BadRequest(str(e), code='invalid_input')
        return Response({
            'adjusted': {'user_id': member.user_id, 'name': member.get_display_name(), 'minutes': total},
            **self._state(event),
        })
