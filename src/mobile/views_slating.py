"""
The app's Slating screens (mobile plan, Phase 3i) — the member side.

* ``GET slating/`` — the hub (``slating_dashboard``): every period not
  archived, with what this member can do in it (apply, vote, see results),
  their own applications, and whether they're on a period's slating
  committee or run it.
* ``POST slating/applications/<id>/withdraw/`` — ``withdraw_application``.
* ``GET slating/periods/<id>/vote/`` — the ballot: the approved primary slate,
  or for an individual vote the positions still undecided and which of them
  this member has voted on this round.
* ``POST slating/periods/<id>/vote/`` — ``{choice, rejected_positions}`` for a
  slate vote or ``{choices: {"<candidate id>": choice}}`` for individual
  positions, plus ``password`` or ``reauth_grant`` (the Votes tab's re-auth).
  Recorded by ``src/slating_ballots.py``, the web's own code.
* ``GET slating/periods/<id>/results/`` — published results.

The web's order: eligibility (``can_vote``, voting open, marked present, a
slate) before re-auth, so a member who can't vote isn't asked for a password.
Applying, My applications and the committee's review, interviews,
attendance, paper ballots and results summary are in
``views_slating_apply.py`` / ``views_slating_committee.py`` (11-02-26).
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_votes import VoteRefused, _confirm_identity
from src.models import SlatingActivity, SlatingApplication, SlatingPeriod
from src.slating_rounds import position_states
from src.slating_ballots import (
    CANDIDATE_REFUSAL, SlatingBallotError, cast_individual, cast_slate, has_slate_ballot, individual_ballot,
    is_present, primary_slate, sat_out_states, sits_out_slate, slate_candidates,
)
from src.view.slating.attendance import not_present_message
from src.view.slating.dashboard import period_actions
from src.view.slating.period_setup import check_and_auto_transition_status

WITHDRAWABLE = ('draft', 'submitted', 'under_review')
_STATUS = {'already_voted': 409, 'invalid_choice': 400, 'no_positions': 400, 'candidate_cannot_vote': 403,
           'voting_closed': 409, 'not_eligible': 403}


def _iso(dt):
    return dt.isoformat() if dt else None


def _candidate_json(c):
    return {
        'id': c.id,
        'position_id': c.position_id,
        'position': c.position.title,
        'name': c.candidate_name,
        'write_in': c.is_write_in,
        'notes': c.notes,
    }


def _period_json(period, user):
    # 11-05-26: the web dashboard's own flags (src/view/slating/dashboard.py
    # period_actions), so both offer the same buttons: no Apply for someone the
    # apply page would refuse, no Cast Vote for someone who can't vote, the
    # slating manager's Manage, and a locked-out admin sees who runs it.
    actions = period_actions(period, user)
    return {
        'id': period.id,
        'name': period.name,
        'term': period.academic_term,
        'description': period.description,
        'status': period.status,
        'status_label': period.get_status_display(),
        'vote_type': period.vote_type,
        'nominations_close_at': _iso(period.nominations_close_at),
        'voting_close_at': _iso(period.voting_close_at),
        'can_apply': actions['can_apply'],
        'apply_path': reverse('slating_apply', args=[period.id]),
        'voting_open': actions['voting_open'],
        'can_vote': actions['can_vote'],
        'has_voted': actions['has_voted'],
        # v4.4.5: candidates sit out their own race (period setting).
        'sits_out': actions['sits_out'],
        'sits_out_message': CANDIDATE_REFUSAL if actions['sits_out'] else '',
        'results_published': period.status == 'results_published',
        'is_committee': actions['can_review'],
        'is_chair': actions['can_manage'],
        # 11-06-26: the committee can open results once a round has started.
        'committee_results': bool(actions['can_review'] and period.current_voting_attempt),
        'manage_path': reverse('slating_period_setup', args=[period.id]),
        'locked_out': actions['locked_out'],
        'managed_by': actions['managed_by'],
        # 11-04-26: the approved slate, natively (view_approved_slate).
        'can_view_slate': _slate_public(period, user),
    }


def _slate_public(period, user):
    from src.mobile.views_slating_slate import slate_public
    return slate_public(period, user)


class _SlatingBase(MobileAPIView):
    web_page = 'slating_dashboard'
    web_page_toggle = False   # the slating views have no page toggles


class SlatingHubView(_SlatingBase):

    def get(self, request):
        user = request.user
        periods = list(SlatingPeriod.objects.exclude(status='archived').order_by('-created_at'))
        for period in periods:
            check_and_auto_transition_status(period)   # as slating_dashboard does on every load
        apps = (SlatingApplication.objects.filter(applicant=user)
                .select_related('period').order_by('-created_at'))
        return Response({
            'periods': [_period_json(p, user) for p in periods],
            'applications': [{
                'id': a.id,
                'period': a.period.name,
                'period_id': a.period_id,
                'status': a.status,
                'status_label': a.get_status_display(),
                'submitted_at': _iso(a.submitted_at),
                'can_withdraw': a.status in WITHDRAWABLE,
            } for a in apps],
            'can_vote': bool(user.can_vote),
            # 11-04-26: the dashboard's "+ New" (create_period is site admins only).
            'can_create_period': bool(user.is_admin),
        })


class SlatingWithdrawView(_SlatingBase):
    web_page = 'slating_withdraw_application'

    def post(self, request, app_id):
        application = get_object_or_404(SlatingApplication, id=app_id, applicant=request.user)
        if application.status not in WITHDRAWABLE:
            raise VoteRefused('not_withdrawable', 'This application cannot be withdrawn.')
        application.status = 'withdrawn'
        application.save(update_fields=['status'])
        SlatingActivity.objects.create(
            period=application.period, user=request.user,
            action='application_submitted',   # the web's own (it has no "withdrawn" action)
            details=f'{request.user.name} withdrew application',
            metadata={'application_id': application.id, 'action': 'withdrawn'})
        return Response({'ok': True})


class SlatingVoteView(_SlatingBase):
    web_page = 'slating_vote'

    def _ballot(self, request, period_id):
        """The web's eligibility checks, in its order. Returns (period, slate)."""
        user = request.user
        period = get_object_or_404(SlatingPeriod, id=period_id)
        if not user.can_vote:
            raise VoteRefused('not_eligible', 'You are not eligible to vote.', 403)
        if not period.can_vote():
            raise VoteRefused('voting_closed', 'Voting is not currently open.', 409)
        if not is_present(period, user):
            raise VoteRefused('not_present', not_present_message(period), 403)   # 11-05-26: names the chair
        slate = primary_slate(period)
        if slate is None:
            raise VoteRefused('no_slate', 'No slate available for voting.', 409)
        return period, slate

    def get(self, request, period_id):
        period, slate = self._ballot(request, period_id)
        out = {
            'period': _period_json(period, request.user),
            'vote_type': period.vote_type,
            'required_percentage': period.required_approval_percentage,
            'attempt': period.current_voting_attempt,
        }
        if period.vote_type == 'individual':
            from src.slating_rounds import KIND_APPROVE, ballot_options, round_threshold
            decided, ballot, voted, config = individual_ballot(period, slate, request.user)
            rows = []
            for st in ballot:
                options, allow_abstain = ballot_options(st, config)
                names = {o.key: o.name for o in st.options()}
                names.update({f'n{n.id}': n.name for n in st.nominees})
                rows.append({
                    'id': st.position.id,
                    'position_id': st.position.id,
                    'position': st.position.title,
                    'kind': st.kind,
                    'name': st.primary.candidate_name if (st.kind == KIND_APPROVE and st.primary) else '',
                    'write_in': bool(st.kind == KIND_APPROVE and st.primary and st.primary.is_write_in),
                    'notes': st.primary.notes if (st.kind == KIND_APPROVE and st.primary) else '',
                    'options': ([] if st.kind == KIND_APPROVE
                                else [{'key': k, 'name': names.get(k, k)} for k in options]),
                    'allow_abstain': allow_abstain,
                    'voted': st.position.id in voted,
                })
            out['positions'] = rows
            # v4.4.5: own positions are left off a candidate's ballot when the period says so.
            out['sitting_out'] = [{'id': st.position.id, 'position': st.position.title}
                                  for st in sat_out_states(period, slate, request.user)]
            # Decided earlier: no result here, as on the web (results wait for the committee).
            out['decided'] = [{'id': st.position.id, 'position': st.position.title} for st in decided]
            out['required_percentage'] = round_threshold(period, config)
        else:
            from src.slating_rounds import slate_rows
            out['candidates'] = [{'id': r.id, 'position_id': r.position.id, 'position': r.position.title,
                                  'name': r.candidate_name, 'kind': r.kind, 'write_in': False, 'notes': r.notes}
                                 for r in slate_rows(period, slate)]
            out['allow_abstain'] = period.allow_abstain
            out['has_voted'] = has_slate_ballot(period, request.user)
            out['sits_out'] = not out['has_voted'] and sits_out_slate(period, slate, request.user)
        return Response(out)

    def post(self, request, period_id):
        period, slate = self._ballot(request, period_id)
        data = request.data if isinstance(request.data, dict) else {}
        user = request.user
        try:
            if period.vote_type == 'individual':
                decided, ballot, voted, config = individual_ballot(period, slate, user)
                if not ballot and sat_out_states(period, slate, user):
                    raise SlatingBallotError('candidate_cannot_vote', CANDIDATE_REFUSAL)
                if all(st.position.id in voted for st in ballot):
                    raise SlatingBallotError('already_voted', 'You have already voted in this round.')
                _confirm_identity(request, data)
                # {"<position id>": "approve" | "reject" | "abstain" | "c<id>" | "n<id>"}
                choices = data.get('choices') if isinstance(data.get('choices'), dict) else {}
                cast_individual(user, period, ballot, voted, config,
                                lambda st: choices.get(str(st.position.id)))
            else:
                if has_slate_ballot(period, user):
                    raise SlatingBallotError('already_voted', 'You have already voted in this round.')
                _confirm_identity(request, data)
                rejected = data.get('rejected_positions')
                cast_slate(user, period, slate, list(slate_candidates(slate)), data.get('choice'),
                           rejected if isinstance(rejected, list) else [])
        except SlatingBallotError as e:
            raise VoteRefused(e.code, str(e), _STATUS.get(e.code, 400))
        return Response({'ok': True})


class SlatingResultsView(_SlatingBase):
    web_page = 'slating_results'

    def get(self, request, period_id):
        from src.view.slating.tallies import current_slate_result, round_history
        from src.view.slating.permissions import can_view_applications
        period = get_object_or_404(SlatingPeriod, id=period_id)
        # 11-06-26: the slating committee sees them before they're published
        # (the website's rule), with which positions "no" voters objected to.
        committee = can_view_applications(request.user, period)
        if period.status != 'results_published' and not committee:
            raise exceptions.PermissionDenied('Results are not yet published.', code='not_published')
        slate = primary_slate(period)
        if slate is None:
            raise exceptions.NotFound('No slate found.', code='no_slate')
        from src.view.slating.results import rejection_breakdown, results_overview
        tally = current_slate_result(period, slate)   # 11-05-26: the counted round's stored outcome
        history = round_history(period, slate)
        overview = results_overview(period, slate, history)
        # 11-05-26: per candidate, in ballot order, "passed" only for the one
        # who holds the position (a run-off's other candidate isn't elected),
        # with counts only from a position round that decided it (a position
        # elected with the slate has no counts of its own).
        by_title = {p['title']: p for p in overview['positions']}
        states = {st.position.id: st for st in position_states(period, slate)}
        order = list(states)
        rows = []
        for c in sorted(slate_candidates(slate), key=lambda c: (order.index(c.position_id)
                                                                 if c.position_id in order else len(order),
                                                                 c.is_runoff)):
            st = states.get(c.position_id)
            entry = by_title.get(c.position.title, {})
            if st is not None and st.decided:
                passed = bool(st.winner and st.winner.key == f'c{c.id}')
            elif c.individual_passed is False:
                passed = False
            else:
                passed = None
            counts = next((t for t in entry.get('tallies', []) if t['candidate'] == c.candidate_name), {})
            rows.append({**_candidate_json(c), 'passed': passed, 'runoff': c.is_runoff,
                         'approve': counts.get('approve', 0), 'reject': counts.get('reject', 0),
                         'abstain': counts.get('abstain', 0), 'has_counts': bool(counts)})
        return Response({
            'period': _period_json(period, request.user),
            'vote_type': period.vote_type,
            'required_percentage': tally['required'],
            'slate': {k: tally[k] for k in ('approve', 'reject', 'abstain', 'total', 'percentage', 'passed')},
            'candidates': rows,
            'published_at': _iso(period.results_publish_at),
            # 11-05-26: what the web page leads with.
            'headline': overview['headline'],
            'positions': [{'id': p['position'].id, 'title': p['title'], 'kind': p['kind'], 'winner': p['winner'],
                           'unfilled': p['unfilled'], 'decided': p['decided'], 'how': p['how'], 'round': p['round'],
                           'tallies': p['tallies']} for p in overview['positions']],
            'history': [{'attempt': r['attempt'], 'vote_type': r['vote_type'], 'status': r['status'],
                         'slate': r.get('slate'), 'positions': r.get('positions', [])} for r in history],
            'published': period.status == 'results_published',
            'live': bool(overview.get('live')),
            # Committee only: [{position, position_id, candidate_name, objection_count}] or None.
            'objections': rejection_breakdown(period, slate) if committee else None,
        })


# ── The slating chair (10-29-26) ─────────────────────────────────────────────

class _ChairView(_SlatingBase):
    web_page = 'slating_period_setup'

    def period(self, request, period_id):
        from src.view.slating.permissions import can_manage_period
        period = get_object_or_404(SlatingPeriod, id=period_id)
        if not can_manage_period(request.user, period):
            raise exceptions.PermissionDenied('Only the slating chair can run the vote.', code='forbidden')
        return period


def _round_error(e):
    return VoteRefused(e.code, str(e), 400)


def _chair_overview(period):
    from src.models import SlatingAttendance, SlatingBallot
    from src.view.slating.attendance import ATTENDANCE_STATUSES
    from src.view.slating.paper_ballots import round_accepts_paper
    from src.view.slating.results import PUBLISHABLE
    from src.slating_rounds import KIND_LABELS, election_context, round_threshold
    ctx = election_context(period)
    attempt = period.current_voting_attempt
    positions = []
    for st in ctx['states']:
        positions.append({
            'id': st.position.id,
            'title': st.position.title,
            'kind': st.kind,
            'kind_label': KIND_LABELS[st.kind],
            'candidates': [c.candidate_name for c in (st.primary, st.runoff) if c is not None],
            'winner': st.winner.name if st.winner else None,
            'unfilled': st.unfilled,
            'failed_last': st.failed_last,
            'on_ballot': st.position.id in ctx.get('on_ballot', set()),
            'allow_abstain': st.position.allow_abstain,
            'options': [{'key': o.key, 'name': o.name} for o in st.options()],
            'nominees': [{'id': n.id, 'name': n.name, 'user_id': n.member.user_id, 'accepted': n.accepted,
                          'elected': n.elected} for n in st.all_nominees],
        })
    live = period.status == 'voting_open'
    return {
        'period': {'id': period.id, 'name': period.name, 'status': period.status,
                   'status_label': period.get_status_display(), 'vote_type': period.vote_type,
                   'attempt': attempt, 'required_percentage': period.required_approval_percentage,
                   'quorum': period.quorum},
        'positions': positions,
        'has_rounds': ctx.get('has_rounds', False),
        'round_ended': ctx.get('round_ended', False),
        'slate_possible': ctx.get('slate_possible', False),
        'default_vote_type': ctx.get('default_vote_type', 'slate'),   # 11-05-26: slate again after a failed slate
        'default_position': ctx.get('default_position'),
        'default_abstain': ctx.get('default_abstain', False),
        'threshold': round_threshold(period, ctx.get('config', {})) if ctx.get('states') else None,
        'present': SlatingAttendance.objects.filter(period=period).count(),
        'ballots_cast': (SlatingBallot.objects.filter(period=period, voting_attempt=attempt)
                         .values('voter').distinct().count()) if live else None,
        'can_open': period.status == 'deliberation' and bool(ctx.get('states')),
        'can_close': live,
        # 11-02-26: the app's own screens for these (views_slating_committee.py).
        'can_attendance': period.status in ATTENDANCE_STATUSES,
        'can_paper': round_accepts_paper(period),
        'can_publish': period.status in PUBLISHABLE,
        # 11-04-26: the slate builder's screens (views_slating_slate.py).
        'slate_tools': _slate_tools(period),
        'web': {'setup': reverse('slating_period_setup', args=[period.id]),
                'attendance': reverse('slating_attendance', args=[period.id]),
                'paper': reverse('slating_paper_ballots', args=[period.id]),
                'results': reverse('slating_results', args=[period.id])},
    }


def _slate_tools(period):
    from src.mobile.views_slating_slate import slate_tools
    return slate_tools(period)


class SlatingChairView(_ChairView):
    """GET — every position and the round in progress, for the chair's screen."""

    def get(self, request, period_id):
        return Response(_chair_overview(self.period(request, period_id)))


class SlatingChairOpenView(_ChairView):
    """POST {vote_type, positions, threshold, abstain, nominees: {"<position id>": [ids]}, quorum}."""

    def post(self, request, period_id):
        from src.slating_rounds import RoundError
        from src.view.slating.period_setup import open_voting_round
        period = self.period(request, period_id)
        if period.status != 'deliberation':
            raise VoteRefused('not_deliberation', 'Voting can be opened from deliberation only.', 409)
        data = request.data if isinstance(request.data, dict) else {}
        nominees = data.get('nominees') if isinstance(data.get('nominees'), dict) else {}
        try:
            open_voting_round(period, request.user, period.status, data.get('vote_type', 'slate'),
                              data.get('positions') or [], data.get('threshold') or None,
                              data.get('abstain') if isinstance(data.get('abstain'), bool) else None,
                              {str(k): v for k, v in nominees.items() if isinstance(v, list)},
                              str(data.get('quorum') or ''))
        except RoundError as e:
            raise _round_error(e)
        return Response(_chair_overview(period))


class SlatingChairCloseView(_ChairView):

    def post(self, request, period_id):
        from src.slating_rounds import RoundError, close_round
        from src.view.slating.vote import round_summary_lines
        period = self.period(request, period_id)
        if period.status != 'voting_open':
            raise VoteRefused('not_open', 'Voting is not currently open.', 409)
        try:
            summary = close_round(period, request.user)
        except RoundError as e:
            raise _round_error(e)
        return Response({'summary': round_summary_lines(summary), 'finished': summary['finished'],
                         **_chair_overview(period)})


class SlatingChairPositionView(_ChairView):
    """POST {action, position_id, nominee_id, user_id, accepted, choice, reason} — as the web panel."""

    def post(self, request, period_id):
        from src.models import ParliamentUser
        from src.slating_rounds import (
            RoundError, add_nominee, decide_manually, remove_nominee, set_nominee_accepted, set_open_nominations,
        )
        period = self.period(request, period_id)
        data = request.data if isinstance(request.data, dict) else {}
        action = data.get('action')
        pid = data.get('position_id')
        nid = data.get('nominee_id')
        try:
            if action in ('nominations_on', 'nominations_off'):
                set_open_nominations(period, request.user, pid, action == 'nominations_on')
            elif action == 'add_nominee':
                member = ParliamentUser.objects.filter(user_id=data.get('user_id'), member_status='Active').first()
                if member is None:
                    raise RoundError('not_found', 'Choose an active member.')
                add_nominee(period, request.user, pid, member, accepted=bool(data.get('accepted')))
            elif action in ('accept_nominee', 'unaccept_nominee'):
                set_nominee_accepted(period, nid, action == 'accept_nominee')
            elif action == 'remove_nominee':
                remove_nominee(period, nid)
            elif action == 'decide':
                decide_manually(period, request.user, pid, str(data.get('choice') or ''), data.get('reason'),
                                override=data.get('override') is True)
            else:
                raise RoundError('invalid_input', 'Unknown action.')
        except RoundError as e:
            raise _round_error(e)
        return Response(_chair_overview(period))
