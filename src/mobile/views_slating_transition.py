"""
The officer transition in the app (11-10-26; the website's slating_transition,
src/view/slating/transition.py, whose functions this calls).

After results are published, a site admin hands each elected position's role
to its winner: the form is pre-filled from the election, any position can be
changed or skipped, the changes are previewed (who gains a role, who loses
it), then run now or scheduled.

``GET  slating/periods/<id>/transition/``
    ``{period, state, scheduled_at, completed_at, rows, members}``.
    ``state`` is ``ready``, ``scheduled`` (a transition is waiting; ``rows``
    show its picks) or ``done``. Refused (403 ``forbidden`` /
    409 ``not_published``) as the web page refuses.
``POST`` ``{action: "preview", assignments: {position id: user id}}``
    ``{changes: [{position, role, new, removed: [names], already}]}``.
``POST`` ``{action: "confirm", assignments, effective_at?: ISO datetime}``
    ``{outcome: "scheduled", scheduled_at}`` or
    ``{outcome: "done", added, removed, errors}``.
``POST`` ``{action: "cancel_scheduled"}``  ``{ok: true}``.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_slating import _iso, _period_json, _SlatingBase
from src.mobile.views_votes import VoteRefused
from src.models import SlatingPeriod
from src.view.slating.transition import (
    TransitionError, build_auto_assignments, cancel_scheduled_transition, parse_assignments,
    planned_role_changes, schedule_or_run, transition_members, transition_refusal, transition_rows,
)

_STATUS = {'forbidden': 403, 'not_published': 409, 'done': 409}


def _member(u):
    return {'user_id': u.user_id, 'name': u.name} if u is not None else None


def _period(request, period_id, allow_done=False):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    refusal = transition_refusal(request.user, period)
    if refusal and not (allow_done and refusal[0] == 'done'):
        code, message = refusal
        raise VoteRefused(code, message, _STATUS.get(code, 400))
    return period


class SlatingTransitionView(_SlatingBase):
    web_page = 'slating_transition'

    def get(self, request, period_id):
        period = _period(request, period_id, allow_done=True)
        base = {
            'period': _period_json(period, request.user),
            'scheduled_at': _iso(period.officer_transition_at),
            'completed_at': _iso(period.officer_transition_completed_at),
        }
        if period.officer_transition_completed:
            return Response({**base, 'state': 'done', 'rows': [], 'members': []})
        auto = build_auto_assignments(period)
        return Response({
            **base,
            'state': 'scheduled' if period.officer_transition_at else 'ready',
            'rows': [{
                'position_id': r['position'].id,
                'title': r['position'].title,
                'role': r['role'].name if r['role'] else None,
                'current': [u.name for u in r['current_holders']],
                'elected': _member(r['auto_assigned']),
                'selected': _member(r['selected_member']),
            } for r in transition_rows(period, auto)],
            'members': [_member(m) for m in transition_members(auto)],
        })

    def post(self, request, period_id):
        period = _period(request, period_id)
        data = request.data if hasattr(request.data, 'get') else {}
        action = data.get('action')
        if action == 'cancel_scheduled':
            cancel_scheduled_transition(period, request.user)
            return Response({'ok': True})
        if action not in ('preview', 'confirm'):
            raise BadRequest('action is preview, confirm or cancel_scheduled.', code='invalid_input')
        picks = data.get('assignments')
        if not isinstance(picks, dict):
            raise BadRequest('assignments is {position id: user id}.', code='invalid_input')
        allowed = {m.pk for m in transition_members(build_auto_assignments(period))}
        try:
            transition_data = parse_assignments(period, {str(k): v for k, v in picks.items()}, allowed)
        except TransitionError as e:
            raise VoteRefused(e.code, str(e), 400)

        if action == 'preview':
            return Response({'changes': [{
                'position': c['position'].title,
                'role': c['role'].name if c['role'] else None,
                'new': c['new'].name,
                'removed': c['removed'],
                'already': c['already'],
            } for c in planned_role_changes(period, transition_data)]})

        effective_at = None
        raw = (data.get('effective_at') or '').strip() if isinstance(data.get('effective_at'), str) else ''
        if raw:
            try:
                effective_at = parse_datetime(raw)
            except (ValueError, TypeError):
                effective_at = None
            if effective_at is None:
                raise BadRequest('Enter a valid date and time for the transition.', code='invalid_input')
            if timezone.is_naive(effective_at):
                effective_at = timezone.make_aware(effective_at, timezone.get_current_timezone())
        try:
            outcome = schedule_or_run(period, request.user, transition_data, effective_at)
        except TransitionError as e:
            raise VoteRefused(e.code, str(e), _STATUS.get(e.code, 400))
        if outcome['outcome'] == 'scheduled':
            return Response({'outcome': 'scheduled', 'scheduled_at': _iso(outcome['at'])})
        return Response({'outcome': 'done', 'added': outcome['added'], 'removed': outcome['removed'],
                         'errors': outcome['errors']})
