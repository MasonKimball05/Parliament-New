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


def get_vote_tally(legislation):
    """Helper to get vote tally for a piece of legislation"""
    votes = CommitteeVote.objects.filter(legislation=legislation)
    if legislation.vote_mode == 'plurality':
        tally = {opt: votes.filter(vote_choice=opt).count() for opt in (legislation.plurality_options or [])}
        tally['total'] = votes.count()
    else:
        tally = {
            'yes': votes.filter(vote_choice='yes').count(),
            'no': votes.filter(vote_choice='no').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
            'total': votes.count()
        }
    return tally


@require_http_methods(["GET", "POST"])
@login_required
def committee_vote(request, code):
    committee = get_object_or_404(Committee, code=code)
    user = request.user

    # Check if user can vote in this committee
    is_voting_member = committee.voting_members.filter(pk=user.pk).exists()
    is_chair = committee.is_chair(user)

    # Determine if user is present (same logic as chapter voting)
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        present=True
    ).order_by('-created_at').first()
    can_vote = bool(attendance) and is_voting_member

    # Auto-close any legislation that has passed its voting_ends_at time
    now = timezone.now()
    expired_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now
    )
    for leg in expired_legislation:
        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at

        # Calculate if the vote passed
        tally = get_vote_tally(leg)
        total_votes = tally['total']

        if total_votes > 0:
            if leg.vote_mode == 'plurality':
                options = {k: v for k, v in tally.items() if k != 'total'}
                if options:
                    max_votes = max(options.values())
                    leg.passed = max_votes > 0
                    leg.status = 'passed' if leg.passed else 'draft'
            elif leg.vote_mode == 'piecewise':
                required = leg.required_number or 0
                leg.passed = tally.get('yes', 0) >= required
                leg.status = 'passed' if leg.passed else 'draft'
            else:
                yes_votes = tally.get('yes', 0)
                no_votes = tally.get('no', 0)
                countable_votes = yes_votes + no_votes
                if countable_votes > 0:
                    yes_percentage = (yes_votes / countable_votes) * 100
                    required_pct = int(leg.required_percentage)
                    leg.passed = yes_percentage >= required_pct
                    leg.status = 'passed' if leg.passed else 'draft'

        leg.save()
        result_text = "passed" if leg.passed else "did not pass"
        logger.info(f"Auto-closed voting on '{leg.title}' (ID: {leg.id}) - scheduled end time reached - {result_text}")

    # Handle recalculate vote result (chair only)
    if request.method == 'POST' and 'recalculate_result' in request.POST:
        legislation_id = request.POST.get('legislation_id')
        legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        if is_chair and legislation.voting_closed:
            tally = get_vote_tally(legislation)
            total_votes = tally['total']

            if total_votes > 0:
                if legislation.vote_mode == 'plurality':
                    options = {k: v for k, v in tally.items() if k != 'total'}
                    if options:
                        max_votes = max(options.values())
                        legislation.passed = max_votes > 0
                        legislation.status = 'passed' if legislation.passed else 'draft'
                elif legislation.vote_mode == 'piecewise':
                    required = legislation.required_number or 0
                    legislation.passed = tally.get('yes', 0) >= required
                    legislation.status = 'passed' if legislation.passed else 'draft'
                else:
                    yes_votes = tally.get('yes', 0)
                    no_votes = tally.get('no', 0)
                    countable_votes = yes_votes + no_votes
                    if countable_votes > 0:
                        yes_percentage = (yes_votes / countable_votes) * 100
                        required_pct = int(legislation.required_percentage)
                        legislation.passed = yes_percentage >= required_pct
                        legislation.status = 'passed' if legislation.passed else 'draft'

                legislation.save()
                result_text = "passed" if legislation.passed else "did not pass"
                logger.info(f"{user.username} recalculated vote result for '{legislation.title}' (ID: {legislation.id}) - {result_text}")
                messages.success(request, f"Vote result recalculated. The vote {result_text}.")
            else:
                messages.warning(request, "No votes to calculate result from.")
        else:
            messages.error(request, "You don't have permission to recalculate this vote.")
        return redirect('vote', code=code)

    # Handle end vote action (chair or poster only)
    if request.method == 'POST' and 'end_vote' in request.POST:
        legislation_id = request.POST.get('legislation_id')
        legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        # Check permission: must be chair or the person who posted it
        if is_chair or legislation.posted_by == user:
            if not legislation.voting_closed:
                legislation.voting_closed = True
                legislation.voting_ended_at = timezone.now()

                # Calculate if the vote passed
                tally = get_vote_tally(legislation)
                total_votes = tally['total']

                if total_votes > 0:
                    if legislation.vote_mode == 'plurality':
                        # Plurality: winner is the option with most votes
                        options = {k: v for k, v in tally.items() if k != 'total'}
                        if options:
                            max_votes = max(options.values())
                            legislation.passed = max_votes > 0
                            legislation.status = 'passed' if legislation.passed else 'draft'
                    elif legislation.vote_mode == 'piecewise':
                        # Piecewise: need exact number of yes votes
                        required = legislation.required_number or 0
                        legislation.passed = tally.get('yes', 0) >= required
                        legislation.status = 'passed' if legislation.passed else 'draft'
                    else:
                        # Percentage: calculate yes percentage of non-abstain votes
                        yes_votes = tally.get('yes', 0)
                        no_votes = tally.get('no', 0)
                        countable_votes = yes_votes + no_votes
                        if countable_votes > 0:
                            yes_percentage = (yes_votes / countable_votes) * 100
                            required_pct = int(legislation.required_percentage)
                            legislation.passed = yes_percentage >= required_pct
                            legislation.status = 'passed' if legislation.passed else 'draft'

                legislation.save()

                result_text = "passed" if legislation.passed else "did not pass"
                logger.info(f"{user.username} ended voting on '{legislation.title}' (ID: {legislation.id}) - {result_text}")
                messages.success(request, f"Voting on '{legislation.title}' has been ended. The vote {result_text}.")
            else:
                messages.warning(request, "Voting has already been closed.")
        else:
            messages.error(request, "You don't have permission to end this vote.")
        return redirect('vote', code=code)

    # Handle voting
    if request.method == 'POST' and ('vote_choice' in request.POST or 'vote_choices' in request.POST) and can_vote:
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if auth_user:
            legislation_id = request.POST.get('legislation_id')
            legislation = get_object_or_404(CommitteeLegislation, id=legislation_id)

            if CommitteeVote.objects.filter(user=user, legislation=legislation).exists():
                messages.error(request, "You have already voted on this legislation.")
                return redirect('committee_vote', code=code)

            if legislation.voting_closed:
                messages.error(request, "Voting on this legislation has ended.")
                return redirect('committee_vote', code=code)

            # Handle multi-select plurality voting
            if legislation.vote_mode == 'plurality' and legislation.plurality_votes_allowed > 1:
                vote_choices = request.POST.getlist('vote_choices')

                # Validate number of selections
                if len(vote_choices) < 1:
                    messages.error(request, "Please select at least one option.")
                    return redirect('committee_vote', code=code)
                if len(vote_choices) > legislation.plurality_votes_allowed:
                    messages.error(request, f"You can only select up to {legislation.plurality_votes_allowed} options.")
                    return redirect('committee_vote', code=code)

                # Validate each choice
                for choice in vote_choices:
                    if choice not in legislation.plurality_options:
                        messages.error(request, "Invalid vote option.")
                        return redirect('committee_vote', code=code)

                # Create a vote record for each selection
                for choice in vote_choices:
                    CommitteeVote.objects.create(user=user, legislation=legislation, vote_choice=choice)

                logger.info(
                    f"{user.username} voted for {vote_choices} on committee legislation '{legislation.title}' (ID: {legislation.id})")
                messages.success(request, f"Your {len(vote_choices)} vote(s) have been submitted.")
            else:
                # Single-select voting (percentage, piecewise, or plurality with 1 vote)
                vote_choice = request.POST.get('vote_choice')
                if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                    messages.error(request, "Invalid vote option.")
                    return redirect('committee_vote', code=code)

                CommitteeVote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)

                logger.info(
                    f"{user.username} voted '{vote_choice}' on committee legislation '{legislation.title}' (ID: {legislation.id})")
                messages.success(request, "Your vote has been submitted.")

            return redirect('committee_vote', code=code)
        else:
            messages.error(request, "Incorrect password.")
            return redirect('committee_vote', code=code)

    # Get available (active) legislation for this committee
    available_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        available_at__lte=timezone.now(),
        voting_closed=False
    ).order_by('-available_at')

    # Get closed/ended legislation for voting history (all committee members can see)
    closed_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        voting_closed=True
    ).order_by('-voting_ended_at', '-available_at')[:20]  # Last 20 closed votes

    # Track which legislation the user has already voted on
    user_votes = CommitteeVote.objects.filter(
        user=user,
        legislation__committee=committee
    ).values_list('legislation_id', flat=True)
    user_voted = set(user_votes)

    # Build vote data for active legislation (for users who voted or are chairs/posters)
    vote_data = {}
    for leg in available_legislation:
        # Show results if user has voted, is chair, or is the poster
        if leg.id in user_voted or is_chair or leg.posted_by == user:
            vote_data[leg.id] = get_vote_tally(leg)

    # Build vote data for ALL closed legislation (visible to all committee members)
    history_vote_data = {}
    for leg in closed_legislation:
        history_vote_data[leg.id] = get_vote_tally(leg)

    # Track which legislation user can manage (end vote)
    can_end_vote = {}
    for leg in available_legislation:
        can_end_vote[leg.id] = is_chair or leg.posted_by == user

    return render(request, 'committee/vote.html', {
        'committee': committee,
        'profile': user,
        'can_vote': can_vote,
        'is_chair': is_chair,
        'is_voting_member': is_voting_member,
        'legislation': available_legislation,
        'closed_legislation': closed_legislation,
        'vote_data': vote_data,
        'history_vote_data': history_vote_data,
        'user_voted': user_voted,
        'can_end_vote': can_end_vote,
        'now': now,
    })


@require_http_methods(["POST"])
@login_required
def create_committee_runoff(request, code, legislation_id):
    """Create a runoff vote from a completed committee plurality vote."""
    committee = get_object_or_404(Committee, code=code)
    original = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)
    user = request.user

    # Verify permissions - must be chair or the person who posted it
    is_chair = committee.is_chair(user)
    if not is_chair and original.posted_by != user:
        messages.error(request, "Only committee chairs or the vote creator can create a runoff.")
        return redirect('committee_vote', code=code)

    # Verify this is a plurality vote with runoff enabled
    if original.vote_mode != 'plurality':
        messages.error(request, "Runoff votes can only be created for plurality votes.")
        return redirect('committee_vote', code=code)

    if not original.plurality_runoff_enabled:
        messages.error(request, "Runoff voting is not enabled for this legislation.")
        return redirect('committee_vote', code=code)

    if not original.voting_closed:
        messages.error(request, "The original vote must be closed before creating a runoff.")
        return redirect('committee_vote', code=code)

    # Check if runoff already exists
    if original.runoff_votes.exists():
        messages.error(request, "A runoff vote has already been created for this legislation.")
        return redirect('committee_vote', code=code)

    # Get top options for runoff
    top_options = original.get_top_options_for_runoff()
    if len(top_options) < 2:
        messages.error(request, "Not enough options for a runoff vote.")
        return redirect('committee_vote', code=code)

    # Create the runoff legislation
    runoff = CommitteeLegislation.objects.create(
        committee=committee,
        title=f"Runoff: {original.title}",
        description=f"Runoff vote for: {original.description}\n\nTop {len(top_options)} options from original vote.",
        document=None,
        posted_by=user,
        available_at=timezone.now(),
        anonymous_vote=original.anonymous_vote,
        allow_abstain=original.allow_abstain,
        vote_mode='plurality',
        plurality_options=top_options,
        plurality_votes_allowed=1,  # Runoff is typically single vote
        plurality_runoff_enabled=False,  # No nested runoffs
        plurality_is_runoff=True,
        plurality_parent=original,
    )

    logger.info(f"{user.username} created runoff vote for committee legislation '{original.title}' (ID: {original.id})")
    messages.success(request, f"Runoff vote created with top {len(top_options)} options: {', '.join(top_options)}")
    return redirect('committee_vote', code=code)