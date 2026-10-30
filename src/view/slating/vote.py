"""
Slating Voting Views

Chapter voting on the officer slate.
Implements secret ballot with configurable approval threshold.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import (
    SlatingPeriod, Slate, SlatingBallot,
    SlatingActivity, SlatingAttendance
)
from .tallies import end_round
from .permissions import voting_member_required, slating_chair_required, can_view_applications
from src.view.webauthn import check_vote_reauth
from src.slating_ballots import SlatingBallotError, cast_individual, cast_slate, individual_ballot
from src.slating_rounds import slate_rows
from src.models.users import member_defer


def _mark_round_ended(period):
    """
    v3.58.0: the round is over (closed or voided), so the next "open voting"
    starts a NEW round instead of resuming this one. Pausing (voting_open →
    deliberation) doesn't set this, so a pause still resumes the same round.
    Kept in extra_settings to avoid a column for one flag.
    """
    period.extra_settings = {**(period.extra_settings or {}), 'round_ended': True}


@login_required
@voting_member_required
def slating_vote(request, period_id):
    """
    Chapter voting on slate.
    Implements secret ballot with configurable threshold.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    is_committee = can_view_applications(user, period)
    voting_paused = period.status == 'deliberation' and period.current_voting_attempt > 0

    # Committee members can see the paused-voting state; everyone else needs voting_open
    if not period.can_vote():
        if not (is_committee and voting_paused):
            messages.error(request, 'Voting is not currently open.')
            return redirect('slating_dashboard')

    # Get current slate
    current_attempt = period.current_voting_attempt
    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate available for voting.')
        return redirect('slating_dashboard')

    candidates = slate.candidates.select_related(
        'position', 'application__applicant'
    ).defer(*member_defer('application__applicant')).order_by('display_order')

    # If vote type is individual, redirect to individual voting
    if not voting_paused and period.vote_type == 'individual':
        return redirect('slating_vote_individual', period_id=period_id)

    # Paused state: committee sees control panel, no vote logic runs
    if voting_paused:
        context = {
            'period': period,
            'slate': slate,
            'candidates': slate_rows(period, slate),   # 10-29-26: one row per position (run-offs: both names)
            'current_attempt': current_attempt,
            'voting_paused': True,
            'is_committee': is_committee,
            'required_percentage': period.required_approval_percentage,
        }
        return render(request, 'slating/vote.html', context)

    # --- Live voting path ---

    # Check if member is marked present
    if not SlatingAttendance.objects.filter(period=period, member=user).exists():
        messages.error(request, 'You are not marked present for this session. Contact the slating chair.')
        return redirect('slating_dashboard')

    # Check if already voted this attempt
    existing_ballot = SlatingBallot.objects.filter(
        period=period,
        voter=user,
        voting_attempt=current_attempt,
        vote_type='slate'
    ).first()

    if request.method == 'POST':
        if existing_ballot:
            messages.error(request, 'You have already voted in this round.')
            return redirect('slating_vote', period_id=period_id)

        # Identity confirmation: password, or passkey (v3.13.3)
        reauth_ok, reauth_error = check_vote_reauth(request)
        if not reauth_ok:
            messages.error(request, reauth_error)
            return redirect('slating_vote', period_id=period_id)

        # The checks and the locked write: src/slating_ballots.py (shared with the app).
        try:
            cast_slate(user, period, slate, candidates, request.POST.get('vote_choice'),
                       request.POST.getlist('rejected_positions'))
        except SlatingBallotError as e:
            messages.error(request, str(e))
            return redirect('slating_vote', period_id=period_id)

        messages.success(request, 'Your vote has been recorded. Thank you for participating!')
        return redirect('slating_dashboard')

    # GET - show voting form.
    # v3.58.0 (Mason): no running tally for members, even after they vote —
    # results stay hidden until the slating committee announces them
    # (publish_results). Paper ballots may still be uncounted, too.
    vote_tally = None

    total_ballots = SlatingBallot.objects.filter(
        period=period, voting_attempt=current_attempt, vote_type='slate'
    ).count()

    context = {
        'period': period,
        'slate': slate,
        'candidates': slate_rows(period, slate),   # 10-29-26: one row per position (run-offs: both names)
        'current_attempt': current_attempt,
        'has_voted': existing_ballot is not None,
        'total_ballots': total_ballots,
        'vote_tally': vote_tally,
        'required_percentage': period.required_approval_percentage,
        'is_committee': is_committee,
        'voting_paused': False,
        'has_passkeys': user.webauthn_credentials.exists(),
    }

    return render(request, 'slating/vote.html', context)


@login_required
@voting_member_required
def individual_vote(request, period_id):
    """
    Individual position voting (fallback after slate votes fail).
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    if not period.can_vote():
        messages.error(request, 'Voting is not currently open.')
        return redirect('slating_dashboard')

    # Check if member is marked present
    if not SlatingAttendance.objects.filter(period=period, member=user).exists():
        messages.error(request, 'You are not marked present for this session. Contact the slating chair.')
        return redirect('slating_dashboard')

    # Check if we're in individual voting mode
    if period.vote_type != 'individual':
        messages.error(request, 'Individual voting is not available for this session.')
        return redirect('slating_vote', period_id=period_id)

    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate available for voting.')
        return redirect('slating_dashboard')

    # This round's ballot: the positions the chair put on it (src/slating_rounds.py),
    # pass/fail or pick-one (run-off, floor nominations).
    decided, ballot, voted_position_ids, config = individual_ballot(period, slate, user)

    if request.method == 'POST':
        # Identity confirmation: password, or passkey (v3.13.3)
        reauth_ok, reauth_error = check_vote_reauth(request)
        if not reauth_ok:
            messages.error(request, reauth_error)
            return redirect('slating_vote_individual', period_id=period_id)

        # The checks and the locked write: src/slating_ballots.py (shared with the app).
        try:
            cast_individual(user, period, ballot, voted_position_ids, config,
                            lambda s: request.POST.get(f'vote_{s.position.id}'))
        except SlatingBallotError as e:
            for msg in e.messages:
                messages.error(request, msg)
            return redirect('slating_vote_individual', period_id=period_id)

        messages.success(request, 'Your votes have been recorded.')
        return redirect('slating_dashboard')

    # GET — this round's positions; decided positions shown separately (no results)
    from src.slating_rounds import KIND_APPROVE, ballot_options, round_threshold
    rows = []
    for state in ballot:
        options, allow_abstain = ballot_options(state, config)
        names = {o.key: o.name for o in state.options()}
        names.update({f'n{n.id}': n.name for n in state.nominees})
        rows.append({
            'state': state,
            'position': state.position,
            'pass_fail': state.kind == KIND_APPROVE,
            'nominee': state.primary.candidate_name if state.primary else '',
            'is_write_in': bool(state.primary and state.primary.is_write_in),
            'choices': [{'key': k, 'name': names.get(k, k)} for k in options],
            'kind_label': {'runoff': 'Run-off', 'nominations': 'Floor nominations'}.get(state.kind, ''),
            'voted': state.position.id in voted_position_ids,
            'allow_abstain': allow_abstain,
        })

    all_voted = all(r['voted'] for r in rows) if rows else True

    context = {
        'period': period,
        'slate': slate,
        'rows': rows,
        'decided_positions': decided,
        'all_voted': all_voted,
        'required_percentage': round_threshold(period, config),
        'has_passkeys': user.webauthn_credentials.exists(),
    }

    return render(request, 'slating/vote_individual.html', context)


@login_required
@slating_chair_required
def close_voting(request, period_id):
    """
    Close the open round and decide what it decides (src/slating_rounds.py:
    a passed slate elects its nominees; a position round elects its winners).
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status != 'voting_open':
        messages.error(request, 'Voting is not currently open.')
        return redirect('slating_period_setup', period_id=period_id)

    from src.slating_rounds import RoundError, close_round
    try:
        summary = close_round(period, request.user)
    except RoundError as e:
        messages.error(request, str(e))
        return redirect('slating_period_setup', period_id=period_id)

    for line in round_summary_lines(summary):
        (messages.success if summary['finished'] else messages.info)(request, line)
    return redirect('slating_results', period_id=period_id)


def round_summary_lines(summary):
    """What closing a round decided, in words (the web's messages and the app's alert)."""
    lines = []
    slate = summary.get('slate')
    if slate is not None:
        pct = slate['percentage']
        lines.append(f'The slate {"passed" if slate["passed"] else "did not pass"}'
                     + (f' with {pct:.1f}% approval.' if pct is not None else ' (no votes counted).'))
    else:
        for d in summary['decided']:
            lines.append(f'{d["position"]}: {d["winner"]} selected.')
        for d in summary['undecided']:
            t = d['tally']
            why = 'tied' if t.get('tie') else 'no one reached the threshold' if t['kind'] != 'approve' else 'did not pass'
            lines.append(f'{d["position"]}: {why}.')
    if summary['finished']:
        lines.append('Every position is decided. Review the results, then publish them.')
    elif summary['remaining']:
        lines.append('Still to decide: ' + ', '.join(summary['remaining']) + '. Open the next round when ready.')
    return lines


@login_required
@slating_chair_required
def reset_votes(request, period_id):
    """
    Void the most recent (paused) voting round: it stays in the history,
    marked voided, and doesn't count. Only available during deliberation
    (i.e. after a vote has been paused). Requires double confirmation.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status != 'deliberation' or period.current_voting_attempt == 0:
        messages.error(request, 'No paused vote to reset.')
        return redirect('slating_period_setup', period_id=period_id)

    attempt = period.current_voting_attempt

    # v3.58.0 (Mason: keep voting history): VOID the round instead of
    # deleting it. Its ballots and votes stay, the round is recorded as
    # voided with its counts in the history and the results export, and
    # re-opening starts a new round number (the old one isn't reused, so
    # its ballots can't block anyone from voting again).
    slate = Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()
    ballots = SlatingBallot.objects.filter(period=period, voting_attempt=attempt).count()
    end_round(period, 'voided', request.user, slate)
    _mark_round_ended(period)
    period.save(update_fields=['extra_settings'])

    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='voting_closed',
        details=f'Round {attempt} voided ({ballots} ballots kept in the history, not counted)',
        metadata={'attempt': attempt, 'voided': True, 'ballots': ballots}
    )

    messages.success(request, f'Round {attempt} voided. Its {ballots} ballot(s) stay in the voting history '
                              'but won\'t count. Re-opening voting starts a fresh round.')
    return redirect('slating_period_setup', period_id=period_id)
