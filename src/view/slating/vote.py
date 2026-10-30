"""
Slating Voting Views

Chapter voting on the officer slate.
Implements secret ballot with configurable approval threshold.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth import authenticate
from django.utils import timezone
from src.models import (
    SlatingPeriod, Slate, SlatingBallot,
    SlateCandidate, SlatingActivity, SlatingAttendance
)
from .tallies import end_round, individual_tallies, open_round, slate_tally
from .permissions import voting_member_required, slating_chair_required, can_view_applications
from src.view.webauthn import check_vote_reauth
from src.slating_ballots import SlatingBallotError, cast_individual, cast_slate
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
            'candidates': candidates,
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
        'candidates': candidates,
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

    # All primary candidates ordered for display
    all_candidates = list(
        slate.candidates.filter(is_runoff=False)
        .select_related('position', 'application__applicant', 'write_in_member').defer(*member_defer('application__applicant', 'write_in_member'))
        .order_by('display_order')
    )

    # Separate already-decided positions from ones still needing a vote
    decided_candidates = [c for c in all_candidates if c.individual_passed is not None]
    pending_candidates = [c for c in all_candidates if c.individual_passed is None]

    # Which pending positions this user has already voted on in this round.
    # v3.58.0: rounds are kept (not deleted on re-vote), so "this round" is
    # the current voting_attempt — individual ballots carry it now.
    current_attempt = period.current_voting_attempt
    voted_position_ids = set(
        SlatingBallot.objects.filter(
            period=period,
            voter=user,
            vote_type='individual',
            voting_attempt=current_attempt,
            position_id__in=[c.position_id for c in pending_candidates]
        ).values_list('position_id', flat=True)
    )

    if request.method == 'POST':
        # Identity confirmation: password, or passkey (v3.13.3)
        reauth_ok, reauth_error = check_vote_reauth(request)
        if not reauth_ok:
            messages.error(request, reauth_error)
            return redirect('slating_vote_individual', period_id=period_id)

        # The checks and the locked write: src/slating_ballots.py (shared with the app).
        try:
            cast_individual(user, period, pending_candidates, voted_position_ids,
                            lambda c: request.POST.get(f'vote_{c.id}'))
        except SlatingBallotError as e:
            for msg in e.messages:
                messages.error(request, msg)
            return redirect('slating_vote_individual', period_id=period_id)

        messages.success(request, 'Your votes have been recorded.')
        return redirect('slating_dashboard')

    # GET — build rows for pending positions only; decided positions shown separately
    rows = []
    for candidate in pending_candidates:
        rows.append({
            'candidate': candidate,
            'voted': candidate.position_id in voted_position_ids,
            'allow_abstain': candidate.position.allow_abstain,
        })

    all_voted = all(r['voted'] for r in rows) if rows else True

    context = {
        'period': period,
        'slate': slate,
        'rows': rows,
        'decided_candidates': decided_candidates,
        'all_voted': all_voted,
        'required_percentage': period.required_approval_percentage,
        'has_passkeys': user.webauthn_credentials.exists(),
    }

    return render(request, 'slating/vote_individual.html', context)


@login_required
@slating_chair_required
def close_voting(request, period_id):
    """
    Close voting and calculate results.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status != 'voting_open':
        messages.error(request, 'Voting is not currently open.')
        return redirect('slating_period_setup', period_id=period_id)

    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate found.')
        return redirect('slating_period_setup', period_id=period_id)

    current_attempt = period.current_voting_attempt

    if period.vote_type == 'individual':
        # Calculate per-position results
        candidates = list(slate.candidates.filter(is_runoff=False).select_related('position'))
        # v3.58.0: this round only, online + paper ballots.
        tallies = individual_tallies(candidates, current_attempt, period)
        passed_count = 0
        failed_count = 0
        for candidate in candidates:
            if candidate.individual_passed is not None:
                # Already decided in a previous round — don't recalculate
                if candidate.individual_passed:
                    passed_count += 1
                else:
                    failed_count += 1
                continue
            approve = tallies[candidate.id]['approve']
            reject = tallies[candidate.id]['reject']
            counted = approve + reject
            if counted > 0:
                pct = (approve / counted) * 100
                candidate.individual_passed = pct >= period.required_approval_percentage
            else:
                candidate.individual_passed = False
            candidate.individual_votes_for = approve
            candidate.individual_votes_against = reject
            candidate.save(update_fields=['individual_passed', 'individual_votes_for', 'individual_votes_against'])
            if candidate.individual_passed:
                passed_count += 1
            else:
                failed_count += 1

        end_round(period, 'failed' if failed_count else 'passed', request.user, slate)
        if failed_count > 0:
            # Some positions failed — return to deliberation for re-vote on failing positions
            period.status = 'deliberation'
            _mark_round_ended(period)
            period.save(update_fields=['status', 'extra_settings'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Individual voting: {passed_count} passed, {failed_count} failed. Returned to deliberation.',
                metadata={'passed': passed_count, 'failed': failed_count, 'individual_voting': True}
            )

            messages.warning(
                request,
                f'{passed_count} position(s) passed, {failed_count} did not. '
                'Passed positions are locked. Re-open voting to vote on the remaining positions.'
            )
        else:
            # All positions passed
            period.status = 'voting_closed'
            period.save(update_fields=['status'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Individual voting complete — all {passed_count} positions passed.',
                metadata={'passed': passed_count, 'individual_voting': True}
            )

            messages.success(request, f'All {passed_count} positions passed. Review the results below.')

    else:
        # Full slate vote — this round's online votes + paper ballots
        # (v3.58.0; was slate.calculate_results(), online only).
        t = slate_tally(period, slate, current_attempt)
        slate.total_votes = t['total']
        slate.approval_votes = t['approve']
        slate.rejection_votes = t['reject']
        slate.abstain_votes = t['abstain']
        slate.approval_percentage = t['percentage']
        slate.passed = t['passed']
        slate.save(update_fields=['total_votes', 'approval_votes', 'rejection_votes',
                                  'abstain_votes', 'approval_percentage', 'passed'])
        end_round(period, 'passed' if slate.passed else 'failed', request.user, slate)

        if slate.passed:
            period.status = 'voting_closed'
            period.save(update_fields=['status'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Slate passed with {slate.approval_percentage:.1f}% approval',
                metadata={
                    'attempt': current_attempt,
                    'approval_percentage': float(slate.approval_percentage),
                    'passed': True
                }
            )

            messages.success(request, f'Voting closed. Slate passed with {slate.approval_percentage:.1f}% approval!')

        else:
            # Slate failed — reset and return to deliberation
            slate.total_votes = 0
            slate.approval_votes = 0
            slate.rejection_votes = 0
            slate.abstain_votes = 0
            slate.approval_percentage = None
            slate.passed = None
            slate.save(update_fields=['total_votes', 'approval_votes', 'rejection_votes', 'abstain_votes', 'approval_percentage', 'passed'])

            period.status = 'deliberation'
            _mark_round_ended(period)
            period.save(update_fields=['status', 'extra_settings'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Slate failed attempt {current_attempt}. Returned to deliberation.',
                metadata={'attempt': current_attempt, 'passed': False}
            )

            messages.warning(
                request,
                'Slate did not pass. You can re-open voting as full slate or switch to individual position votes.'
            )

    return redirect('slating_results', period_id=period_id)


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
