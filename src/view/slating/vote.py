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
    SlateCandidate, SlatingActivity
)
from .permissions import voting_member_required, slating_chair_required
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

    if not period.can_vote():
        messages.error(request, 'Voting is not currently open.')
        return redirect('slating_dashboard')

    # Get current slate for voting
    current_attempt = period.current_voting_attempt
    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate available for voting.')
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

        # Verify password for vote authentication
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if not auth_user:
            messages.error(request, 'Incorrect password. Please try again.')
            return redirect('slating_vote', period_id=period_id)

        vote_choice = request.POST.get('vote_choice')
        if vote_choice not in ['approve', 'reject', 'abstain']:
            messages.error(request, 'Invalid vote choice.')
            return redirect('slating_vote', period_id=period_id)

        # Create ballot (tracks who voted - for audit)
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

        # Create anonymous vote
        vote_hash = hashlib.sha256(
            f"{secrets.token_hex(32)}:{timezone.now().isoformat()}".encode()
        ).hexdigest()

        SlatingVote.objects.create(
            period=period,
            slate=slate,
            voting_attempt=current_attempt,
            vote_choice=vote_choice,
            vote_hash=vote_hash
        )

        # Log activity (without revealing vote)
        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='vote_cast',
            details='Vote recorded',
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.success(request, 'Your vote has been recorded. Thank you for participating!')
        return redirect('slating_dashboard')

    # GET - Show voting form
    candidates = slate.candidates.select_related(
        'position', 'application__applicant'
    ).order_by('display_order')

    # Get vote counts (only show after user has voted or voting closed)
    show_tally = existing_ballot is not None

    vote_tally = None
    if show_tally:
        votes = SlatingVote.objects.filter(
            period=period,
            slate=slate,
            voting_attempt=current_attempt
        )
        vote_tally = {
            'total': votes.count(),
            'approve': votes.filter(vote_choice='approve').count(),
            'reject': votes.filter(vote_choice='reject').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
        }

    # Get total eligible voters and who has voted
    total_ballots = SlatingBallot.objects.filter(
        period=period,
        voting_attempt=current_attempt,
        vote_type='slate'
    ).count()

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
        'current_attempt': current_attempt,
        'max_attempts': period.max_slate_voting_attempts,
        'has_voted': existing_ballot is not None,
        'total_ballots': total_ballots,
        'vote_tally': vote_tally,
        'required_percentage': period.required_approval_percentage,
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

    # Check if we're in individual voting mode
    if period.current_voting_attempt < period.max_slate_voting_attempts:
        messages.error(request, 'Individual voting is not yet available.')
        return redirect('slating_vote', period_id=period_id)

    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate available for voting.')
        return redirect('slating_dashboard')

    # Get candidates that need individual votes
    candidates = slate.candidates.filter(
        individual_passed__isnull=True
    ).select_related('position', 'application__applicant')

    if request.method == 'POST':
        # Verify password
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if not auth_user:
            messages.error(request, 'Incorrect password. Please try again.')
            return redirect('slating_vote_individual', period_id=period_id)

        # Process votes for each candidate
        for candidate in candidates:
            # Check if already voted for this candidate
            existing = SlatingBallot.objects.filter(
                period=period,
                voter=user,
                vote_type='individual',
                position=candidate.position
            ).exists()

            if existing:
                continue

            vote_choice = request.POST.get(f'vote_{candidate.id}')
            if vote_choice not in ['approve', 'reject', 'abstain']:
                continue

            # Create ballot
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

            # Create anonymous vote
            vote_hash = hashlib.sha256(
                f"{secrets.token_hex(32)}:{timezone.now().isoformat()}".encode()
            ).hexdigest()

            SlatingVote.objects.create(
                period=period,
                slate_candidate=candidate,
                vote_choice=vote_choice,
                vote_hash=vote_hash
            )

        messages.success(request, 'Your votes have been recorded.')
        return redirect('slating_dashboard')

    # GET - show individual voting form
    # Check which candidates user has already voted on
    voted_positions = SlatingBallot.objects.filter(
        period=period,
        voter=user,
        vote_type='individual'
    ).values_list('position_id', flat=True)

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
        'voted_positions': list(voted_positions),
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

    # Calculate results
    slate.calculate_results()

    current_attempt = period.current_voting_attempt

    if slate.passed:
        # Slate passed!
        period.status = 'voting_closed'
        period.save()

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

    elif current_attempt >= period.max_slate_voting_attempts:
        # Max attempts reached, move to individual voting
        period.status = 'voting_open'  # Keep voting open for individual votes
        period.save()

        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='voting_closed',
            details=f'Slate failed after {current_attempt} attempts. Moving to individual position votes.',
            metadata={
                'attempt': current_attempt,
                'approval_percentage': float(slate.approval_percentage) if slate.approval_percentage else 0,
                'passed': False,
                'individual_voting': True
            },
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.warning(
            request,
            f'Slate failed with {slate.approval_percentage:.1f}% approval after {current_attempt} attempts. '
            'Moving to individual position votes.'
        )

    else:
        # Slate failed, but more attempts available
        # Reset for next attempt
        slate.total_votes = 0
        slate.approval_votes = 0
        slate.rejection_votes = 0
        slate.abstain_votes = 0
        slate.approval_percentage = None
        slate.passed = None
        slate.save()

        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='voting_closed',
            details=f'Slate failed attempt {current_attempt}. {period.max_slate_voting_attempts - current_attempt} attempts remaining.',
            metadata={
                'attempt': current_attempt,
                'passed': False
            },
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.warning(
            request,
            f'Slate did not pass. {period.max_slate_voting_attempts - current_attempt} voting attempts remaining.'
        )

    return redirect('slating_period_setup', period_id=period_id)
