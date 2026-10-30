"""
The app's Slating screens (mobile plan, Phase 3i) — the member side.

* ``GET slating/`` — the hub (``slating_dashboard``): every period not
  archived, with what this member can do in it (apply on the website, vote,
  see results), their own applications, and whether they're on a period's
  slating committee (that work stays on the website).
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
Applying (custom questions, GPA screenshot) and everything the slating
committee does are on the website.
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_votes import VoteRefused, _confirm_identity
from src.models import SlatingActivity, SlatingApplication, SlatingPeriod
from src.slating_ballots import (
    SlatingBallotError, cast_individual, cast_slate, has_slate_ballot, individual_ballot, is_present,
    primary_slate, slate_candidates,
)
from src.view.slating.period_setup import check_and_auto_transition_status
from src.view.slating.permissions import can_view_applications

WITHDRAWABLE = ('draft', 'submitted', 'under_review')
_STATUS = {'already_voted': 409, 'invalid_choice': 400, 'no_positions': 400}


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


def _has_voted(period, user):
    """This round: the slate ballot cast, or every undecided position voted on."""
    if period.vote_type != 'individual':
        return has_slate_ballot(period, user)
    slate = primary_slate(period)
    if slate is None:
        return False
    _, pending, voted = individual_ballot(period, slate, user)
    return bool(pending) and all(c.position_id in voted for c in pending)


def _period_json(period, user):
    open_now = period.can_vote()
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
        'can_apply': period.can_apply(),
        'apply_path': reverse('slating_apply', args=[period.id]),
        'voting_open': open_now,
        'can_vote': bool(open_now and user.can_vote),
        'has_voted': bool(open_now and _has_voted(period, user)),
        'results_published': period.status == 'results_published',
        'is_committee': can_view_applications(user, period),
        'manage_path': reverse('slating_period_setup', args=[period.id]),
    }


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
            raise VoteRefused('not_present',
                              'You are not marked present for this session. Contact the slating chair.', 403)
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
            decided, pending, voted = individual_ballot(period, slate, request.user)
            out['positions'] = [{**_candidate_json(c), 'allow_abstain': c.position.allow_abstain,
                                 'voted': c.position_id in voted} for c in pending]
            out['decided'] = [{**_candidate_json(c), 'passed': c.individual_passed} for c in decided]
        else:
            out['candidates'] = [_candidate_json(c) for c in slate_candidates(slate)]
            out['allow_abstain'] = period.allow_abstain
            out['has_voted'] = has_slate_ballot(period, request.user)
        return Response(out)

    def post(self, request, period_id):
        period, slate = self._ballot(request, period_id)
        data = request.data if isinstance(request.data, dict) else {}
        user = request.user
        try:
            if period.vote_type == 'individual':
                decided, pending, voted = individual_ballot(period, slate, user)
                if all(c.position_id in voted for c in pending):
                    raise SlatingBallotError('already_voted', 'You have already voted in this round.')
                _confirm_identity(request, data)
                choices = data.get('choices') if isinstance(data.get('choices'), dict) else {}
                cast_individual(user, period, pending, voted, lambda c: choices.get(str(c.id)))
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
        from src.view.slating.tallies import round_history, slate_tally
        period = get_object_or_404(SlatingPeriod, id=period_id)
        if period.status != 'results_published':
            raise exceptions.PermissionDenied('Results are not yet published.', code='not_published')
        slate = primary_slate(period)
        if slate is None:
            raise exceptions.NotFound('No slate found.', code='no_slate')
        candidates = list(slate_candidates(slate))
        tally = slate_tally(period, slate, period.current_voting_attempt)
        rows = []
        if period.vote_type == 'individual':
            decided_in = {}
            for rnd in round_history(period, slate):
                if rnd['status'] == 'voided':
                    continue
                for row in rnd.get('positions', []):
                    decided_in[(row['position'], row['candidate'])] = row
            for c in candidates:
                counts = decided_in.get((c.position.title, c.candidate_name)) or {}
                rows.append({**_candidate_json(c), 'passed': c.individual_passed,
                             'approve': counts.get('approve', 0), 'reject': counts.get('reject', 0),
                             'abstain': counts.get('abstain', 0)})
        else:
            rows = [{**_candidate_json(c), 'passed': tally['passed']} for c in candidates if not c.is_runoff]
        return Response({
            'period': _period_json(period, request.user),
            'vote_type': period.vote_type,
            'required_percentage': period.required_approval_percentage,
            'slate': {k: tally[k] for k in ('approve', 'reject', 'abstain', 'total', 'percentage', 'passed')},
            'candidates': rows,
            'published_at': _iso(period.results_publish_at),
        })
