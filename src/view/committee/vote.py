from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import authenticate
from datetime import timedelta
from src.models import Committee, CommitteeLegislation, CommitteeVote, Attendance
import logging

logger = logging.getLogger('function_calls')


@require_http_methods(["GET", "POST"])
@login_required
def committee_vote(request, code):
    committee = get_object_or_404(Committee, code=code)
    user = request.user

    # Check if user can vote in this committee
    is_voting_member = committee.voting_members.filter(pk=user.pk).exists()

    # Determine if user is present (same logic as chapter voting)
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        present=True
    ).order_by('-created_at').first()
    can_vote = bool(attendance) and is_voting_member

    # Handle voting
    if request.method == 'POST' and 'vote_choice' in request.POST and can_vote:
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if auth_user:
            legislation_id = request.POST.get('legislation_id')
            legislation = get_object_or_404(CommitteeLegislation, id=legislation_id)

            if CommitteeVote.objects.filter(user=user, legislation=legislation).exists():
                messages.error(request, "You have already voted on this legislation.")
                return redirect('vote', code=code)

            if legislation.voting_closed:
                messages.error(request, "Voting on this legislation has ended.")
                return redirect('vote', code=code)

            vote_choice = request.POST.get('vote_choice')
            if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                messages.error(request, "Invalid vote option.")
                return redirect('vote', code=code)

            CommitteeVote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)

            logger.info(
                f"{user.username} voted '{vote_choice}' on committee legislation '{legislation.title}' (ID: {legislation.id})")

            messages.success(request, "Your vote has been submitted.")
            return redirect('vote', code=code)
        else:
            messages.error(request, "Incorrect password.")
            return redirect('vote', code=code)

    # Get available legislation for this committee
    available_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        available_at__lte=timezone.now(),
        voting_closed=False
    )

    # Build vote data for chairs/uploaders
    is_chair = committee.is_chair(user)
    vote_data = {}
    if is_chair:
        for leg in CommitteeLegislation.objects.filter(committee=committee, posted_by=user):
            votes = CommitteeVote.objects.filter(legislation=leg)
            if leg.vote_mode == 'plurality':
                tally = {opt: votes.filter(vote_choice=opt).count() for opt in leg.plurality_options}
                tally['total'] = votes.count()
                vote_data[leg.id] = tally
            else:
                vote_data[leg.id] = {
                    'yes': votes.filter(vote_choice='yes').count(),
                    'no': votes.filter(vote_choice='no').count(),
                    'abstain': votes.filter(vote_choice='abstain').count(),
                    'total': votes.count()
                }

    return render(request, 'committee/vote.html', {
        'committee': committee,
        'profile': user,
        'can_vote': can_vote,
        'is_chair': is_chair,
        'is_voting_member': is_voting_member,
        'legislation': available_legislation,
        'vote_data': vote_data,
    })