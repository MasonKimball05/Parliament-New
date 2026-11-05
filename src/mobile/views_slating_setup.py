"""
Election setup in the app (11-04-26): creating and editing a slating period,
its positions, and transferring the slating committee's admin.

Site admins (the web's ``slating_admin_required``):

* ``POST slating/periods/new/`` — ``create_period`` ``{name, academic_term,
  description, min_gpa_requirement, gpa_level_2_threshold,
  required_approval_percentage}``.

The period's managers (``can_manage_period``, the web's
``slating_chair_required``: the slating manager, the committee's admin and
chairs; a site admin only while the period isn't locked):

* ``GET slating/periods/<id>/setup/`` — ``edit_period``'s page: the settings,
  the schedule, the committee, the stage and what it offers next. Runs the
  date-based status change and the committee backfill, as the page does.
* ``POST slating/periods/<id>/setup/`` — ``{action: save | add_committee_member
  | remove_committee_member | delete, …}`` in the web form's field names
  (dates as ISO 8601; a blank one clears it, as on the web).
* ``POST slating/periods/<id>/setup/status/`` — ``change_period_status``
  ``{status}``. Opening voting is the chair screen's (``chair/open/``).
* ``GET / POST slating/periods/<id>/positions/`` — ``manage_positions`` and
  ``add_position``.
* ``POST slating/periods/<id>/positions/defaults/`` — ``copy_default_positions``.
* ``POST slating/periods/<id>/positions/reorder/`` — the web API's
  ``reorder_positions`` ``{position_order: [ids]}``.
* ``GET / POST slating/periods/<id>/positions/<pid>/`` — ``edit_position``.
* ``POST slating/periods/<id>/positions/<pid>/delete/`` — ``delete_position``
  ``{action: deactivate | hard_delete}``.
* ``POST slating/periods/<id>/positions/<pid>/toggle/`` — the web API's
  ``toggle_position_active``.

The committee's admin or a site admin (``transfer_admin`` has no chair check):

* ``GET / POST slating/periods/<id>/transfer-admin/`` — ``{new_admin (a
  member's user id), reason}``.

Each runs the web view's own helpers (``period_setup.py``,
``position_manager.py``, ``transfer_admin.py``), so the checks, the activity
log and the notifications are the same. The web form's own limits (required
fields, number ranges, the code's pattern), which a browser enforces before
it posts, are checked here. The application form builder stays on the website.
"""
import re

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data
from src.mobile.views_slating import _iso, _SlatingBase
from src.mobile.views_slating_committee import _manager_period
from src.mobile.views_votes import VoteRefused
from src.models import Role, SlatingPosition
from src.view.slating.period_setup import (
    PERIOD_DATE_FIELDS, PeriodSetupError, add_period_committee_member, apply_status_change,
    check_and_auto_transition_status, check_status_change, create_slating_period, delete_slating_period,
    eligible_period_managers, ensure_slating_committee, period_committee_members, remove_period_committee_member,
    save_period_settings,
)
from src.view.slating.permissions import can_manage_period
from src.view.slating.position_manager import (
    PositionError, copy_default_positions_into, create_position, default_slate_positions, period_positions,
    position_data, remove_position, reorder_period_positions, toggle_position, update_position,
)
from src.view.slating.transfer_admin import (
    TransferError, eligible_new_admins, transfer_committee_admin, transfer_refusal,
)

#: The banner at the top of the web's setup page, by status.
STAGES = {
    'setup': ('Setting up the election',
              'Add positions and build the application form, then open nominations when ready.'),
    'nominations_open': ('Nominations are open',
                         'Members can submit applications. Close nominations when the application window ends.'),
    'nominations_closed': ('Nominations closed',
                           'Review applications and schedule interviews. Start deliberation when the committee is '
                           'ready.'),
    'deliberation': ('Deliberation in progress',
                     'Conduct interviews, review candidates, and build the slate. Open voting when the slate is '
                     'approved.'),
    'voting_open': ('Chapter voting is open',
                    'Members are casting their votes. Monitor results below and close voting when participation is '
                    'sufficient.'),
    'voting_closed': ('Voting has closed', 'Review the final tally and publish results to notify the chapter.'),
    'results_published': ('Results published',
                          'The chapter can view election results. Archive this period after the officer transition '
                          'is complete.'),
    'archived': ('Election archived', 'This election period is complete and archived for records.'),
}

#: The banner's status buttons (the web's ``slating_change_status`` forms) and their confirmations.
NEXT_STATUS = {
    'setup': ('nominations_open', 'Open nominations',
              'Open nominations? Members will be able to submit applications.'),
    'nominations_open': ('nominations_closed', 'Close nominations',
                         'Close nominations? No new applications will be accepted.'),
    'nominations_closed': ('deliberation', 'Start deliberation', ''),
    'voting_open': ('deliberation', 'Pause voting',
                    'Pause voting and return to deliberation? Votes already cast will be preserved but voting will '
                    'stop.'),
}

CODE_PATTERN = re.compile(r'^[A-Z_]+$')


def _setup_error(e):
    status = {'forbidden': 403, 'not_found': 404, 'no_committee': 409, 'unchanged': 409}.get(e.code, 400)
    return VoteRefused(e.code, str(e), status)


def _member(u):
    return {'user_id': u.user_id, 'name': u.name}


def _number(data, key, kind, low, high, label, problems, required=True):
    value = (data.get(key) or '').strip()
    if not value:
        if required:
            problems.append(f'{label} is required.')
        return
    try:
        n = kind(value)
    except ValueError:
        problems.append(f'{label} must be a number.')
        return
    if not low <= n <= high:
        problems.append(f'{label} must be between {low} and {high}.')


def _check_period_form(data, dates=True):
    """The settings form's own limits (``required``, ``min`` / ``max``), which the browser enforces."""
    problems = []
    if not (data.get('name') or '').strip():
        problems.append('Election name is required.')
    if not (data.get('academic_term') or '').strip():
        problems.append('Academic term is required.')
    _number(data, 'required_approval_percentage', int, 1, 100, 'Required approval %', problems)
    _number(data, 'min_gpa_requirement', float, 0, 4, 'Minimum GPA', problems)
    _number(data, 'gpa_level_2_threshold', float, 0, 1, 'Level 2 threshold', problems)
    if dates:
        for field in PERIOD_DATE_FIELDS:
            value = data.get(field)
            if not value:
                continue
            try:
                parsed = parse_datetime(value)
            except ValueError:   # date-shaped but impossible (Feb 30, hour 25)
                parsed = None
            if parsed is None:
                problems.append(f'{field} isn’t a date and time.')
    if problems:
        raise BadRequest(' '.join(problems), code='invalid_input')


# ── Creating a period (site admins) ─────────────────────────────────────────

class SlatingCreatePeriodView(_SlatingBase):
    web_page = 'slating_create_period'

    def post(self, request):
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Only administrators can access this page.', code='forbidden')
        data = _form_data(request)
        _check_period_form(data, dates=False)
        try:
            period = create_slating_period(request.user, data)
        except PeriodSetupError as e:
            raise _setup_error(e)
        return Response({'ok': True, 'period_id': period.id,
                         'message': f'Slating period "{period.name}" created successfully.'}, status=201)


# ── The setup page ───────────────────────────────────────────────────────────

def setup_data(period, user, notice=None):
    """What the web's setup page shows the chair, for the app's screen."""
    committee = period.slating_committee
    stage = STAGES.get(period.status, ('', ''))
    nxt = NEXT_STATUS.get(period.status)
    transfer_from = period.admin_transferred_from if period.admin_transferred else None
    return {
        'period': {
            'id': period.id,
            'name': period.name,
            'term': period.academic_term,
            'description': period.description,
            'status': period.status,
            'status_label': period.get_status_display(),
            'vote_type': period.vote_type,
            'attempt': period.current_voting_attempt,
            'manager': _member(period.slating_manager) if period.slating_manager_id else None,
            'min_gpa_requirement': float(period.min_gpa_requirement),
            'gpa_level_2_threshold': float(period.gpa_level_2_threshold),
            'required_approval_percentage': period.required_approval_percentage,
            # v4.4.5: candidates sit out their own race (src/slating_ballots.py).
            'candidates_cannot_vote': bool((period.extra_settings or {}).get('candidates_cannot_vote')),
            'candidates_cannot_vote_locked': period.status == 'voting_open',
            **{field: _iso(getattr(period, field)) for field in PERIOD_DATE_FIELDS},
        },
        'notice': notice,
        'stage': {'title': stage[0], 'hint': stage[1]},
        'next_status': {'status': nxt[0], 'label': nxt[1], 'confirm': nxt[2]} if nxt else None,
        'counts': {
            'positions': period.positions.filter(is_active=True).count(),
            'fields': period.form_fields.filter(is_active=True).count(),
            'applications': period.applications.exclude(status='draft').count(),
            'applications_all': period.applications.count(),
        },
        'committee': {
            'exists': committee is not None,
            'admin': _member(committee.admin) if committee and committee.admin_id else None,
            'members': [{**_member(m), 'is_admin': bool(committee and m.pk == committee.admin_id)}
                        for m in period_committee_members(period)],
        },
        'eligible_managers': [{**_member(m), 'member_type': m.get_member_type_display()}
                              for m in eligible_period_managers()],
        'can_set_manager': bool(user.is_admin),
        'can_delete': bool(user.is_admin),
        'can_transfer_admin': transfer_refusal(user, period) is None,
        'admin_transfer': {
            'from': transfer_from.name if transfer_from else None,
            'at': _iso(period.admin_transferred_at),
            'reason': period.admin_transfer_reason,
        } if period.admin_transferred else None,
        'default_positions': [title for _, _, title in default_slate_positions()],
        'web': {
            'setup': reverse('slating_period_setup', args=[period.id]),
            'form_builder': reverse('slating_form_builder', args=[period.id]),
            'build_slate': reverse('slating_build_slate', args=[period.id]),
            'import_applications': reverse('slating_import_applications', args=[period.id]),
            'manual_results': reverse('slating_manual_results', args=[period.id]),
        },
    }


class _SetupView(_SlatingBase):
    web_page = 'slating_period_setup'


class SlatingSetupView(_SetupView):

    def get(self, request, period_id):
        period = _manager_period(request, period_id)
        notice = None
        # Check for automatic status transitions based on dates
        if check_and_auto_transition_status(period):
            notice = f'Status automatically updated to "{period.get_status_display()}" based on scheduled date.'
        ensure_slating_committee(period)
        return Response(setup_data(period, request.user, notice))

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        check_and_auto_transition_status(period)   # edit_period runs it on every request
        data = _form_data(request)
        action = data.get('action', 'save')
        try:
            if action == 'save':
                _check_period_form(data)
                save_period_settings(period, request.user, data)
                message = 'Period settings saved.'
            elif action == 'add_committee_member':
                member = add_period_committee_member(period, data.get('member_id', ''))
                message = f'{member.name} added to slating committee.'
            elif action == 'remove_committee_member':
                if not (data.get('member_id') or '').strip():
                    raise BadRequest('member_id is required.', code='invalid_input')
                member = remove_period_committee_member(period, data.get('member_id', ''))
                message = f'{member.name} removed from slating committee.' if member else ''
            elif action == 'delete':
                name = delete_slating_period(period, request.user)
                return Response({'ok': True, 'deleted': True,
                                 'message': f'Slating period "{name}" has been deleted.'})
            else:
                raise BadRequest('action is save, add_committee_member, remove_committee_member or delete.',
                                 code='invalid_input')
        except PeriodSetupError as e:
            raise _setup_error(e)
        ensure_slating_committee(period)
        return Response({**setup_data(period, request.user), 'message': message})


class SlatingSetupStatusView(_SetupView):
    web_page = 'slating_change_status'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        data = _form_data(request)
        new_status = data.get('status')
        try:
            check_status_change(period, new_status)
        except PeriodSetupError as e:
            raise _setup_error(e)
        if new_status == 'voting_open':
            raise VoteRefused('use_chair', 'Open voting from Run the vote.', 409)
        old_status = apply_status_change(period, request.user, new_status)
        message = f'Period status changed to {period.get_status_display()}.'
        if old_status == 'voting_open' and new_status == 'deliberation':
            message = 'Voting paused. You can reopen it from Run the vote.'
        return Response({**setup_data(period, request.user), 'message': message})


# ── Positions ────────────────────────────────────────────────────────────────

def _position_json(position):
    return {
        **position_data(position),
        'role_name': position.role.name if position.role_id else None,
        'display_order': position.display_order,
    }


def positions_data(period, user):
    active, inactive = period_positions(period)
    return {
        'period': {'id': period.id, 'name': period.name, 'status': period.status,
                   'status_label': period.get_status_display()},
        'positions': [_position_json(p) for p in active.select_related('role')],
        'inactive': [_position_json(p) for p in inactive.select_related('role')],
        'roles': [{'id': r.id, 'name': r.name, 'code': r.code} for r in Role.objects.all().order_by('name')],
        'default_positions': [title for _, _, title in default_slate_positions()],
        'can_hard_delete': bool(user.is_admin),
    }


def _position_error(e):
    return VoteRefused(e.code, str(e), 409 if e.code in ('duplicate_code', 'has_slate') else 400)


def _check_position_form(data, new):
    """The add / edit modal's own limits."""
    problems = []
    if not (data.get('title') or '').strip():
        problems.append('Title is required.')
    if new:
        code = (data.get('code') or '').strip().upper()
        if not code:
            problems.append('Code is required.')
        elif len(code) > 20 or not CODE_PATTERN.match(code):
            problems.append('Code: up to 20 uppercase letters and underscores.')
    _number(data, 'min_gpa', float, 0, 4, 'Minimum GPA', problems, required=False)
    _number(data, 'min_semesters_active', int, 0, 100, 'Semesters active', problems, required=False)
    if problems:
        raise BadRequest(' '.join(problems), code='invalid_input')


class _PositionsView(_SlatingBase):
    web_page = 'slating_positions'

    def position(self, period, position_id):
        return get_object_or_404(SlatingPosition, id=position_id, period=period)


class SlatingPositionsView(_PositionsView):

    def get(self, request, period_id):
        return Response(positions_data(_manager_period(request, period_id), request.user))

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        data = _form_data(request)
        _check_position_form(data, new=True)
        try:
            position = create_position(period, request.user, data)
        except PositionError as e:
            raise _position_error(e)
        return Response({**positions_data(period, request.user), 'position_id': position.id,
                         'message': f'Position "{position.title}" added.'}, status=201)


class SlatingPositionDefaultsView(_PositionsView):
    web_page = 'slating_copy_default_positions'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        added = copy_default_positions_into(period, request.user)
        message = f'Added {added} default positions.' if added else 'All default positions already exist.'
        return Response({**positions_data(period, request.user), 'added': added, 'message': message})


class SlatingPositionReorderView(_PositionsView):
    web_page = 'slating_api_reorder_positions'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        order = request.data.get('position_order') if isinstance(request.data, dict) else None
        if not isinstance(order, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in order):
            raise BadRequest('position_order is a list of position ids.', code='invalid_input')
        reorder_period_positions(period, order)
        return Response(positions_data(period, request.user))


class SlatingPositionView(_PositionsView):
    web_page = 'slating_edit_position'

    def get(self, request, period_id, position_id):
        period = _manager_period(request, period_id)
        return Response({'position': _position_json(self.position(period, position_id))})

    def post(self, request, period_id, position_id):
        period = _manager_period(request, period_id)
        position = self.position(period, position_id)
        data = _form_data(request)
        _check_position_form(data, new=False)
        update_position(period, position, request.user, data)
        return Response({**positions_data(period, request.user),
                         'message': f'Position "{position.title}" updated.'})


class SlatingPositionDeleteView(_PositionsView):
    web_page = 'slating_delete_position'

    def post(self, request, period_id, position_id):
        period = _manager_period(request, period_id)
        position = self.position(period, position_id)
        action = _form_data(request).get('action', 'deactivate')
        try:
            message = remove_position(position, request.user, action)
        except PositionError as e:
            raise _position_error(e)
        return Response({**positions_data(period, request.user), 'message': message})


class SlatingPositionToggleView(_PositionsView):
    web_page = 'slating_api_toggle_position'

    def post(self, request, period_id, position_id):
        period = _manager_period(request, period_id)
        is_active = toggle_position(self.position(period, position_id))
        return Response({**positions_data(period, request.user), 'is_active': is_active})


# ── Transferring the committee's admin ──────────────────────────────────────

class SlatingTransferAdminView(_SlatingBase):
    web_page = 'slating_transfer_admin'

    def period(self, request, period_id):
        from src.models import SlatingPeriod
        period = get_object_or_404(SlatingPeriod, id=period_id)
        refusal = transfer_refusal(request.user, period)
        if refusal:
            code, message = refusal
            raise VoteRefused(code, message, 409 if code == 'no_committee' else 403)
        return period

    def data(self, period):
        committee = period.slating_committee
        admin = committee.admin
        transfer_from = period.admin_transferred_from if period.admin_transferred else None
        return {
            'period': {'id': period.id, 'name': period.name, 'status': period.status,
                       'status_label': period.get_status_display()},
            'current_admin': _member(admin) if admin else None,
            'eligible': [_member(m) for m in eligible_new_admins(committee)],
            'previous': {
                'from': transfer_from.name if transfer_from else None,
                'at': _iso(period.admin_transferred_at),
                'reason': period.admin_transfer_reason,
            } if period.admin_transferred else None,
        }

    def get(self, request, period_id):
        return Response(self.data(self.period(request, period_id)))

    def post(self, request, period_id):
        period = self.period(request, period_id)
        data = _form_data(request)
        try:
            new_admin = transfer_committee_admin(period, request.user, data.get('new_admin'), data.get('reason', ''))
        except TransferError as e:
            raise VoteRefused(e.code, str(e), 400)
        period.refresh_from_db()
        return Response({
            **self.data(period),
            'message': f'Admin successfully transferred to {new_admin.name}.',
            # Handing it on can end the sender's own access to the period.
            'can_manage': can_manage_period(request.user, period),
        })
