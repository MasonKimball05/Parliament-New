from ..decorators import log_function_call
from ..models import Legislation, Vote, ActivityLog, ParliamentUser
from django.db.models import Count
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.utils import timezone
from src.notification_service import notify_users

@login_required
@log_function_call
def end_vote(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Allow the uploader OR admins to end the vote
    if request.user != legislation.posted_by and not request.user.is_admin:
        return HttpResponseForbidden("Only the uploader or an admin can end the vote.")

    # Close voting
    legislation.voting_closed = True
    legislation.save()

    # Gather votes
    votes = Vote.objects.filter(legislation=legislation)
    vote_summary = votes.values('vote_choice').annotate(count=Count('id'))

    # Count totals
    yes_votes = votes.filter(vote_choice='yes').count()
    no_votes = votes.filter(vote_choice='no').count()
    abstain_votes = votes.filter(vote_choice='abstain').count()
    total_votes = votes.exclude(vote_choice='abstain').count()

    if legislation.vote_mode == 'plurality':
        vote_breakdown_dict = {str(option): votes.filter(vote_choice=option).count() for option in legislation.plurality_options}
        winner = max(vote_breakdown_dict, key=vote_breakdown_dict.get) if vote_breakdown_dict else None
        vote_breakdown = {'keys': list(vote_breakdown_dict.keys()), 'values': list(vote_breakdown_dict.values())}
    else:
        vote_breakdown = {
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
        }
        winner = None

    vote_passed = False
    required_pct = None
    yes_percentage = None
    if legislation.vote_mode == 'percentage':
        required_pct = int(legislation.required_percentage or 51)
        yes_percentage = (yes_votes / total_votes) * 100 if total_votes > 0 else 0
        vote_passed = yes_percentage >= required_pct
    elif legislation.vote_mode == 'piecewise':
        required_number = legislation.required_number or 0
        vote_passed = yes_votes >= required_number
    elif legislation.vote_mode == 'plurality':
        plurality_counts = {
            option: votes.filter(vote_choice=option).count()
            for option in legislation.plurality_options
        }
        most_voted = max(plurality_counts, key=plurality_counts.get, default=None)

        # Check for ties - find all options with the max vote count
        if plurality_counts:
            max_count = max(plurality_counts.values())
            tied_options = [opt for opt, cnt in plurality_counts.items() if cnt == max_count]
            has_tie = len(tied_options) > 1 and max_count > 0
        else:
            has_tie = False
            tied_options = []

        # Only passes if there's a clear winner (no tie)
        vote_passed = bool(most_voted) and not has_tie
        winner = most_voted if not has_tie else None

    # Update status based on vote outcome
    if vote_passed:
        legislation.status = 'passed'
    else:
        legislation.status = 'removed'
    legislation.save()

    _end_meta = {
        'result': 'passed' if vote_passed else 'failed',
        'vote_mode': legislation.vote_mode,
        'anonymous': legislation.anonymous_vote,
        'total_votes': total_votes,
    }
    if not legislation.anonymous_vote:
        _end_meta['vote_breakdown'] = vote_breakdown
    ActivityLog.log_activity(
        action_type='vote_ended',
        user=request.user,
        description=f'{request.user.name} ended voting on "{legislation.title}" — {"Passed" if vote_passed else "Did Not Pass"}',
        request=request,
        object_type='Legislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata=_end_meta,
    )

    # Send in-app notification to all users who voted
    try:
        voter_user_ids = votes.values_list('user', flat=True)
        voter_users = ParliamentUser.objects.filter(pk__in=voter_user_ids)
        result_text = 'Passed' if vote_passed else 'Did Not Pass'
        notify_users(
            voter_users,
            'vote_ended',
            f'Vote Ended: {legislation.title} — {result_text}',
            link=f'/legislation/detail/{legislation.pk}/',
            source_type='Legislation',
            source_id=legislation.id,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to create vote-ended notifications: {e}", exc_info=True)

    context = {
        'legislation': legislation,
        'summary': vote_summary,
        'anonymous': legislation.anonymous_vote,
        'remove_abstain': not legislation.allow_abstain,
        'in_favor': votes.filter(vote_choice='yes'),
        'against': votes.filter(vote_choice='no'),
        'abstain': votes.filter(vote_choice='abstain'),
        'passed': vote_passed,
        'total_votes': total_votes,
        'yes_votes': yes_votes,
        'yes_percentage': f"{yes_percentage:.0f}%" if yes_percentage is not None else "N/A",
        'required_percentage': required_pct if required_pct is not None else 'N/A',
        'vote_breakdown': vote_breakdown,
        'winner': winner,
    }

    #legislation.set_passed()

    if legislation.vote_mode == 'plurality':
        # Get sorted results for display
        sorted_results = legislation.get_plurality_results()
        context['plurality_results'] = {
            'results': [
                {
                    'option': r['option'],
                    'count': r['count'],
                    'voters': [v.user.name for v in votes.filter(vote_choice=r['option']).select_related('user')]
                }
                for r in sorted_results
            ]
        }

        # Add runoff information
        context['has_tie'] = legislation.has_plurality_tie()
        context['runoff_enabled'] = legislation.plurality_runoff_enabled
        context['runoff_count'] = legislation.plurality_runoff_count
        context['top_options_for_runoff'] = legislation.get_top_options_for_runoff()
        context['unique_voter_count'] = legislation.get_unique_voter_count()
        context['votes_allowed'] = legislation.plurality_votes_allowed
        context['is_runoff'] = legislation.plurality_is_runoff
        if legislation.plurality_parent:
            context['parent_legislation'] = legislation.plurality_parent

    return render(request, 'vote_result.html', context)


@login_required
@log_function_call
def create_runoff(request, legislation_id):
    """Create a runoff vote from a completed plurality vote."""
    original = get_object_or_404(Legislation, id=legislation_id)

    # Verify permissions
    if request.user != original.posted_by and not request.user.is_admin:
        return HttpResponseForbidden("Only the uploader or an admin can create a runoff.")

    # Verify this is a plurality vote with runoff enabled
    if original.vote_mode != 'plurality':
        messages.error(request, "Runoff votes can only be created for plurality votes.")
        return redirect('vote')

    if not original.plurality_runoff_enabled:
        messages.error(request, "Runoff voting is not enabled for this legislation.")
        return redirect('vote')

    if not original.voting_closed:
        messages.error(request, "The original vote must be closed before creating a runoff.")
        return redirect('vote')

    # Check if runoff already exists
    if original.runoff_votes.exists():
        messages.error(request, "A runoff vote has already been created for this legislation.")
        return redirect('vote')

    # Get top options for runoff
    top_options = original.get_top_options_for_runoff()
    if len(top_options) < 2:
        messages.error(request, "Not enough options for a runoff vote.")
        return redirect('vote')

    # Create the runoff legislation
    runoff = Legislation.objects.create(
        title=f"Runoff: {original.title}",
        description=f"Runoff vote for: {original.description}\n\nTop {len(top_options)} options from original vote.",
        document=None,
        posted_by=request.user,
        available_at=timezone.now(),
        voting_starts_at=timezone.now(),
        anonymous_vote=original.anonymous_vote,
        allow_abstain=original.allow_abstain,
        vote_mode='plurality',
        plurality_options=top_options,
        plurality_votes_allowed=1,  # Runoff is typically single vote
        plurality_runoff_enabled=False,  # No nested runoffs
        plurality_is_runoff=True,
        plurality_parent=original,
    )

    ActivityLog.log_activity(
        action_type='legislation_created',
        user=request.user,
        description=f'{request.user.name} created runoff vote for "{original.title}"',
        request=request,
        object_type='Legislation',
        object_id=runoff.id,
        object_repr=runoff.title,
        metadata={
            'is_runoff': True,
            'parent_legislation_id': original.id,
            'parent_title': original.title,
            'runoff_options': top_options,
        },
    )
    messages.success(request, f"Runoff vote created with top {len(top_options)} options: {', '.join(top_options)}")
    return redirect('vote')