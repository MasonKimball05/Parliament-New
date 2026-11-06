"""
The slate builder in the app (11-04-26): ``src/view/slating/slate_builder.py``
and the ``slate_candidates`` API, run through the web's own helpers so the
permissions, status gates, validation, activity log and messages match.

The period's managers (``can_manage_period``, the web's
``slating_chair_required``: the slating manager, the committee's admin and
chairs; a site admin only while the period isn't locked):

* ``GET / POST slating/periods/<id>/slate/build/`` — ``build_slate``: the
  draft slate, its positions' primary and runoff slots and the applications
  still available. POST ``{action: assign | assign_runoff | unassign |
  save_notes, position_id, application_id, assignment_id, notes}``.
* ``POST slating/periods/<id>/slate/approve/`` — ``approve_slate``
  ``{slate_id (default: the draft), slate_type, name, description}``.
* ``GET slating/periods/<id>/slates/<slate id>/`` — ``slate_preview``.
* ``POST slating/periods/<id>/slates/<slate id>/copy/`` — ``copy_slate``.
* ``GET slating/periods/<id>/slates/<slate id>/candidates/`` — the
  ``slate_candidates`` API.
* ``GET / POST slating/periods/<id>/slate/edit/`` — ``edit_approved_slate``
  ``{position_id, candidate_type: applicant | write_in, application_id |
  member_id}`` (voting open or paused).
* ``GET / POST slating/periods/<id>/slate/write-ins/`` — the period setup
  page's Blank Positions panel and ``assign_write_in`` ``{position_id,
  member_id}``; ``POST …/write-ins/remove/`` — ``remove_write_in``
  ``{candidate_id}`` (deliberation or voting).
* ``GET / POST slating/periods/<id>/manual-results/`` — ``manual_results``
  ``{vote_mode, winner_<position id>, overall_approve …, approve_<position id> …}``.

Voting members (``can_vote``):

* ``GET slating/periods/<id>/slate/`` — ``view_approved_slate`` (voting open,
  closed or results published).
"""
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data
from src.mobile.views_slating import _iso, _SlatingBase
from src.mobile.views_slating_committee import _committee_period, _manager_period, _num, _period_head
from src.view.slating.permissions import can_manage_period
from src.mobile.views_votes import VoteRefused
from src.models import Slate, SlatingPeriod
from src.view.slating.api import slate_candidates_json
from src.view.slating.slate_builder import (
    APPROVED_READ_ONLY, PUBLIC_STATUSES, WRITE_IN_STATUSES, ManualResultsError, builder_slate, SlateError, add_write_in, approve_slate_for_voting,
    assign_candidate, build_slate_context, copy_slate_to_draft, delete_write_in, draft_slate, edit_slate_context,
    editable_slate, manual_results_refusal, public_slate, record_manual_results, replace_candidate,
    save_candidate_notes, slate_candidate_rows, slate_to_approve, unassign_candidate, voting_paused, write_in_options,
)

def _refused(e):
    return VoteRefused(e.code, str(e), e.status)


def _person(sc):
    if sc.application_id:
        return sc.application.applicant
    return sc.write_in_member


def _candidate(sc):
    person = _person(sc)
    return {
        'id': sc.id,
        'position_id': sc.position_id,
        'name': sc.candidate_name,
        'user_id': person.user_id if person else None,
        'application_id': sc.application_id,
        'write_in': sc.is_write_in,
        'is_runoff': sc.is_runoff,
        'notes': sc.notes,
    }


def _ids(data, *keys):
    """The posted ids the web looks up must be numbers (the web view would fail on anything else)."""
    for key in keys:
        value = data.get(key)
        if value and not str(value).isdigit():
            raise BadRequest(f'{key} is a number.', code='invalid_input')


def _members(members):
    return [{'user_id': m.user_id, 'name': m.name} for m in members]


def _slate_head(slate):
    return {
        'id': slate.id,
        'name': slate.name,
        'description': slate.description,
        'slate_type': slate.slate_type,
        'type_label': slate.get_slate_type_display(),
        'is_approved': slate.is_approved,
        'approved_at': _iso(slate.approved_at),
        'label': slate.label,   # 11-05-26: name · candidates · when approved
    }


# ── Build ────────────────────────────────────────────────────────────────────

def _build_payload(period, slate, user):
    ctx = build_slate_context(period, slate)
    positions = list(ctx['positions'])
    first_choice_of = {}
    for key, apps in ctx['apps_by_position'].items():
        for app in apps:
            first_choice_of[app.id] = key if key != 'other' else None
    slots = []
    for p in positions:
        entry = ctx['assignments'].get(p.id, {})
        slots.append({
            'id': p.id,
            'title': p.title,
            'code': p.code,
            'primary': _candidate(entry['primary']) if entry.get('primary') else None,
            'runoff': _candidate(entry['runoff']) if entry.get('runoff') else None,
        })
    return {
        'period': _period_head(period, user),
        'slate': _slate_head(slate),
        'positions': slots,
        'primary_filled_count': ctx['primary_filled_count'],
        'all_primary_filled': ctx['all_primary_filled'],
        'available': [{
            'id': app.id,
            'applicant': app.applicant.name,
            'user_id': app.applicant.user_id,
            'status': app.status,
            'status_label': app.get_status_display(),
            'gpa_level': app.gpa_level,
            'interviewed': bool(app.interviews.all()),
            # The bucket the web sorts them into: their first choice that's an active position.
            'first_choice_position': first_choice_of.get(app.id),
            'tiers': {str(p.id): tier for p in positions if (tier := app.get_position_tier(p.id))},
        } for app in ctx['available_apps']],
        'existing_slates': [{**_slate_head(s)} for s in ctx['existing_slates']],
        'approve_confirm': ctx['approve_confirm'],
        # 11-06-26: the whole committee builds the slate; approving it stays
        # with the managers (can_manage_period).
        'can_approve': can_manage_period(user, period),
    }


class SlateBuildView(_SlatingBase):
    web_page = 'slating_build_slate'

    def get(self, request, period_id):
        period = _committee_period(request, period_id)
        slate, read_only = builder_slate(period, request.user)
        return Response({**_build_payload(period, slate, request.user), 'read_only': read_only})

    def post(self, request, period_id):
        period = _committee_period(request, period_id)
        slate, read_only = builder_slate(period, request.user)
        if read_only:   # 11-05-26: the approved slate, as the web's builder shows it
            raise VoteRefused('approved', APPROVED_READ_ONLY, 409)
        data = _form_data(request)
        action = data.get('action')
        _ids(data, 'position_id', 'application_id', 'assignment_id')
        if action in ('assign', 'assign_runoff'):
            if not data.get('position_id') or not data.get('application_id'):
                raise BadRequest('Choose a position and an applicant.', code='invalid_input')
            message = assign_candidate(period, slate, data.get('position_id'), data.get('application_id'),
                                       is_runoff=action == 'assign_runoff')
        elif action == 'unassign':
            message = unassign_candidate(slate, data.get('assignment_id'))
        elif action == 'save_notes':
            save_candidate_notes(slate, data.get('assignment_id'), data.get('notes', '').strip())
            message = 'Notes saved.'
        else:
            raise BadRequest('action is assign, assign_runoff, unassign or save_notes.', code='invalid_input')
        return Response({**_build_payload(period, slate, request.user), 'message': message})


# ── Approve, preview, copy, the candidates API ───────────────────────────────

class SlateApproveView(_SlatingBase):
    web_page = 'slating_approve_slate'

    def post(self, request, period_id):
        period = _manager_period(request, period_id)
        data = _form_data(request)
        _ids(data, 'slate_id')
        slate = slate_to_approve(period, data.get('slate_id') or None)
        try:
            warning, message = approve_slate_for_voting(period, slate, request.user, data)
        except SlateError as e:
            raise _refused(e)
        return Response({'ok': True, 'slate': _slate_head(slate), 'warning': warning, 'message': message})


def _slate_view(period, slate, user, public):
    out = {
        'period': _period_head(period, user),
        'slate': {
            **_slate_head(slate),
            'total_votes': slate.total_votes,
            'approval_votes': slate.approval_votes,
            'rejection_votes': slate.rejection_votes,
            'approval_percentage': _num(slate.approval_percentage),
            'passed': slate.passed,
        },
        'candidates': [{
            **_candidate(c),
            'position': c.position.title,
            'position_code': c.position.code,
        } for c in slate_candidate_rows(slate)],
        'public': public,
    }
    if not public:
        out['can_approve'] = not slate.is_approved
    return out


class SlatePreviewView(_SlatingBase):
    web_page = 'slating_slate_preview'

    def get(self, request, period_id, slate_id):
        period = _committee_period(request, period_id)
        slate = get_object_or_404(Slate, id=slate_id, period=period)
        return Response(_slate_view(period, slate, request.user, public=False))


class SlateCopyView(_SlatingBase):
    web_page = 'slating_copy_slate'

    def post(self, request, period_id, slate_id):
        period = _committee_period(request, period_id)
        source = get_object_or_404(Slate, id=slate_id, period=period)
        new_slate, message = copy_slate_to_draft(period, source, request.user)
        return Response({'ok': True, 'slate': _slate_head(new_slate), 'message': message})


class SlateCandidatesView(_SlatingBase):
    web_page = 'slating_api_slate_candidates'

    def get(self, request, period_id, slate_id):
        period = _committee_period(request, period_id)
        slate = get_object_or_404(Slate, id=slate_id, period=period)
        return Response({'candidates': slate_candidates_json(slate)})


class ApprovedSlateView(_SlatingBase):
    """``view_approved_slate``: any voting member, once voting has opened."""
    web_page = 'slating_view_approved_slate'

    def get(self, request, period_id):
        if not request.user.can_vote:
            raise VoteRefused('not_eligible', 'You are not eligible to vote.', 403)
        period = get_object_or_404(SlatingPeriod, id=period_id)
        try:
            slate = public_slate(period)
        except SlateError as e:
            raise _refused(e)
        return Response(_slate_view(period, slate, request.user, public=True))


# ── Edit the approved slate ──────────────────────────────────────────────────

def _edit_payload(period, slate, user):
    ctx = edit_slate_context(period, slate)
    return {
        'period': _period_head(period, user),
        'slate': _slate_head(slate),
        'voting_paused': ctx['voting_paused'],
        'rows': [{
            'position': {'id': row['position'].id, 'title': row['position'].title},
            'current': _candidate(row['current']) if row['current'] else None,
        } for row in ctx['rows']],
        'applications': [{'id': a.id, 'name': a.applicant.name, 'user_id': a.applicant.user_id}
                         for a in ctx['applications']],
        'members': _members(ctx['active_members']),
    }


class SlateEditView(_SlatingBase):
    web_page = 'slating_edit_approved_slate'

    def slate(self, request, period_id):
        period = _manager_period(request, period_id)
        try:
            return period, editable_slate(period)
        except SlateError as e:
            raise _refused(e)

    def get(self, request, period_id):
        period, slate = self.slate(request, period_id)
        return Response(_edit_payload(period, slate, request.user))

    def post(self, request, period_id):
        period, slate = self.slate(request, period_id)
        data = _form_data(request)
        _ids(data, 'position_id', 'application_id')
        # The web page checks this in the browser before it posts; the web view
        # removes the old candidate first, so an incomplete choice would leave
        # the position vacant. The app is refused before anything changes.
        kind = data.get('candidate_type')
        if not data.get('position_id') or not (
                (kind == 'applicant' and data.get('application_id'))
                or (kind == 'write_in' and data.get('member_id'))):
            raise BadRequest('Invalid replacement — select an applicant or a member.', code='invalid_input')
        try:
            message = replace_candidate(period, slate, request.user, data)
        except SlateError as e:
            raise _refused(e)
        return Response({**_edit_payload(period, slate, request.user), 'message': message})


# ── Write-ins ────────────────────────────────────────────────────────────────

def _write_in_payload(period, user):
    from src.view.slating.slate_builder import approved_primary_slate
    opts = write_in_options(period)
    refusal = None
    if period.status not in WRITE_IN_STATUSES:
        refusal = 'Write-in assignments are only allowed during deliberation or voting.'
    elif approved_primary_slate(period) is None:
        refusal = 'No approved primary slate found.'
    with_write_in = {c.position_id for c in opts['write_in_candidates']}
    return {
        'period': _period_head(period, user),
        'available': refusal is None,
        'refusal': refusal,
        'blank_positions': [{'id': p.id, 'title': p.title, 'has_write_in': p.id in with_write_in}
                            for p in opts['blank_positions']],
        'write_ins': [{**_candidate(c), 'position': c.position.title} for c in opts['write_in_candidates']],
        'members': _members(opts['active_members']),
        # {position id: [{user_id, tier}]} — who applied for each blank position.
        'applicants': {pid: [{'user_id': e['id'], 'tier': e['tier']} for e in entries]
                       for pid, entries in opts['pos_applicants'].items()},
        'slated_ids': opts['slated_ids'],
    }


class SlateWriteInsView(_SlatingBase):
    web_page = 'slating_assign_write_in'

    def get(self, request, period_id):
        period = _committee_period(request, period_id)
        return Response(_write_in_payload(period, request.user))

    def post(self, request, period_id):
        period = _committee_period(request, period_id)
        data = _form_data(request)
        _ids(data, 'position_id')
        try:
            message = add_write_in(period, request.user, data.get('position_id'), data.get('member_id'))
        except SlateError as e:
            raise _refused(e)
        return Response({**_write_in_payload(period, request.user), 'message': message})


class SlateWriteInRemoveView(_SlatingBase):
    web_page = 'slating_remove_write_in'

    def post(self, request, period_id):
        period = _committee_period(request, period_id)
        data = _form_data(request)
        _ids(data, 'candidate_id')
        message = delete_write_in(period, data.get('candidate_id'))
        return Response({**_write_in_payload(period, request.user), 'message': message})


# ── Manual results ───────────────────────────────────────────────────────────

class ManualResultsView(_SlatingBase):
    web_page = 'slating_manual_results'

    def period(self, request, period_id):
        period = _manager_period(request, period_id)
        refusal = manual_results_refusal(period)
        if refusal:
            raise VoteRefused('already_recorded', refusal, 409)
        return period

    def get(self, request, period_id):
        from src.view.slating.slate_builder import eligible_members
        period = self.period(request, period_id)
        return Response({
            'period': _period_head(period, request.user) | {
                'required_percentage': period.required_approval_percentage},
            'positions': [{'id': p.id, 'title': p.title}
                          for p in period.positions.filter(is_active=True).order_by('display_order')],
            'members': _members(eligible_members()),
        })

    def post(self, request, period_id):
        period = self.period(request, period_id)
        try:
            message = record_manual_results(period, request.user, _form_data(request))
        except ManualResultsError as e:
            raise BadRequest(' '.join(e.errors), code='invalid_input')
        return Response({'ok': True, 'message': message, 'status': period.status})


def slate_tools(period):
    """Which of the slate screens the chair's screen offers (views_slating._chair_overview)."""
    from src.view.slating.slate_builder import MANUAL_RESULTS_CLOSED, approved_primary_slate
    approved = approved_primary_slate(period) is not None
    return {
        'can_build': True,
        'can_edit': approved and (period.status == 'voting_open' or voting_paused(period)),
        'can_write_ins': approved and period.status in WRITE_IN_STATUSES,
        'can_manual_results': period.status not in MANUAL_RESULTS_CLOSED,
    }


def slate_public(period, user):
    """The hub's "See the slate" (``view_approved_slate``'s checks, without raising)."""
    from src.view.slating.slate_builder import approved_primary_slate
    return bool(user.can_vote and period.status in PUBLIC_STATUSES and approved_primary_slate(period) is not None)
