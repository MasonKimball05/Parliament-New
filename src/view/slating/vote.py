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
    SlatingPeriod, Slate, SlatingBallot, SlatingVote,
    SlateCandidate, SlatingActivity, SlatingAttendance
)
from .permissions import voting_member_required, slating_chair_required, can_view_applications
import hashlib
import secrets


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
    ).order_by('display_order')

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

        password = request.POST.get('password')
        if not user.check_password(password):
            messages.error(request, 'Incorrect password. Please try again.')
            return redirect('slating_vote', period_id=period_id)

        vote_choice = request.POST.get('vote_choice')
        valid_choices = ['approve', 'reject']
        if period.allow_abstain:
            valid_choices.append('abstain')
        if vote_choice not in valid_choices:
            messages.error(request, 'Invalid vote choice.')
            return redirect('slating_vote', period_id=period_id)

        rejected_positions = []
        if vote_choice == 'reject':
            rejected_positions = request.POST.getlist('rejected_positions')
            if not rejected_positions:
                messages.error(request, 'Please select at least one position you are objecting to.')
                return redirect('slating_vote', period_id=period_id)
            rejected_positions = [int(p) for p in rejected_positions]

        ballot_hash = hashlib.sha256(
            f"{user.user_id}:{period_id}:{current_attempt}:{secrets.token_hex(16)}".encode()
        ).hexdigest()

        SlatingBallot.objects.create(
            period=period,
            voter=user,
            voting_attempt=current_attempt,
            vote_type='slate',
            ballot_hash=ballot_hash
        )

        vote_hash = hashlib.sha256(
            f"{secrets.token_hex(32)}:{timezone.now().isoformat()}".encode()
        ).hexdigest()

        SlatingVote.objects.create(
            period=period,
            slate=slate,
            voting_attempt=current_attempt,
            vote_choice=vote_choice,
            rejected_positions=rejected_positions,
            vote_hash=vote_hash
        )

        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='vote_cast',
            details='Vote recorded',
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.success(request, 'Your vote has been recorded. Thank you for participating!')
        return redirect('slating_dashboard')

    # GET - show voting form
    show_tally = existing_ballot is not None
    vote_tally = None
    if show_tally:
        votes = SlatingVote.objects.filter(
            period=period, slate=slate, voting_attempt=current_attempt
        )
        vote_tally = {
            'total': votes.count(),
            'approve': votes.filter(vote_choice='approve').count(),
            'reject': votes.filter(vote_choice='reject').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
        }

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
        .select_related('position', 'application__applicant', 'write_in_member')
        .order_by('display_order')
    )

    # Separate already-decided positions from ones still needing a vote
    decided_candidates = [c for c in all_candidates if c.individual_passed is not None]
    pending_candidates = [c for c in all_candidates if c.individual_passed is None]

    # Which pending positions this user has already voted on in this round
    voted_position_ids = set(
        SlatingBallot.objects.filter(
            period=period,
            voter=user,
            vote_type='individual',
            position_id__in=[c.position_id for c in pending_candidates]
        ).values_list('position_id', flat=True)
    )

    if request.method == 'POST':
        password = request.POST.get('password')
        if not user.check_password(password):
            messages.error(request, 'Incorrect password. Please try again.')
            return redirect('slating_vote_individual', period_id=period_id)

        # Validate: every unvoted pending position must have a selection
        unvoted = [c for c in pending_candidates if c.position_id not in voted_position_ids]
        valid_choices_per = {}
        errors = []
        for candidate in unvoted:
            choice = request.POST.get(f'vote_{candidate.id}')
            allowed = ['approve', 'reject']
            if candidate.position.allow_abstain:
                allowed.append('abstain')
            if choice not in allowed:
                errors.append(f'Please select a valid vote for {candidate.position.title}.')
            else:
                valid_choices_per[candidate.id] = choice

        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('slating_vote_individual', period_id=period_id)

        # Record votes
        for candidate in unvoted:
            choice = valid_choices_per[candidate.id]

            ballot_hash = hashlib.sha256(
                f"{user.user_id}:{period_id}:{candidate.id}:{secrets.token_hex(16)}".encode()
            ).hexdigest()

            SlatingBallot.objects.create(
                period=period,
                voter=user,
                vote_type='individual',
                position=candidate.position,
                ballot_hash=ballot_hash
            )

            vote_hash = hashlib.sha256(
                f"{secrets.token_hex(32)}:{timezone.now().isoformat()}".encode()
            ).hexdigest()

            SlatingVote.objects.create(
                period=period,
                slate_candidate=candidate,
                vote_choice=choice,
                vote_hash=vote_hash
            )

        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='vote_cast',
            details='Individual position votes recorded',
            ip_address=request.META.get('REMOTE_ADDR')
        )

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
        candidates = slate.candidates.filter(is_runoff=False).select_related('position')
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
            ind_votes = SlatingVote.objects.filter(slate_candidate=candidate)
            approve = ind_votes.filter(vote_choice='approve').count()
            reject = ind_votes.filter(vote_choice='reject').count()
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

        if failed_count > 0:
            # Some positions failed — return to deliberation for re-vote on failing positions
            period.status = 'deliberation'
            period.save(update_fields=['status'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Individual voting: {passed_count} passed, {failed_count} failed. Returned to deliberation.',
                metadata={'passed': passed_count, 'failed': failed_count, 'individual_voting': True},
                ip_address=request.META.get('REMOTE_ADDR')
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
                metadata={'passed': passed_count, 'individual_voting': True},
                ip_address=request.META.get('REMOTE_ADDR')
            )

            messages.success(request, f'All {passed_count} positions passed. Review the results below.')

    else:
        # Full slate vote — calculate and check pass/fail
        slate.calculate_results()

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
                },
                ip_address=request.META.get('REMOTE_ADDR')
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
            period.save(update_fields=['status'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='voting_closed',
                details=f'Slate failed attempt {current_attempt}. Returned to deliberation.',
                metadata={'attempt': current_attempt, 'passed': False},
                ip_address=request.META.get('REMOTE_ADDR')
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
    Clear all ballots and votes for the most recent voting attempt and
    decrement the attempt counter. Only available during deliberation
    (i.e. after a vote has been paused). Requires double confirmation.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status != 'deliberation' or period.current_voting_attempt == 0:
        messages.error(request, 'No paused vote to reset.')
        return redirect('slating_period_setup', period_id=period_id)

    attempt = period.current_voting_attempt

    # Delete all ballots and votes for this attempt
    deleted_ballots, _ = SlatingBallot.objects.filter(
        period=period,
        voting_attempt=attempt
    ).delete()

    deleted_votes, _ = SlatingVote.objects.filter(
        period=period,
        voting_attempt=attempt
    ).delete()

    # Reset attempt counter so re-opening doesn't skip a slot
    period.current_voting_attempt = max(0, attempt - 1)
    period.save(update_fields=['current_voting_attempt'])

    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='voting_closed',
        details=f'Votes reset: attempt {attempt} cleared ({deleted_ballots} ballots, {deleted_votes} votes deleted)',
        metadata={'attempt': attempt, 'reset': True},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'All {deleted_ballots} ballots from attempt {attempt} have been cleared. Voting can be re-opened fresh.')
    return redirect('slating_period_setup', period_id=period_id)
