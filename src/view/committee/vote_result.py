from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required
from src.models import Committee, CommitteeLegislation, CommitteeVote

__all__ = ['committee_vote_result']


@login_required
def committee_vote_result(request, code, legislation_id):
    """View detailed vote results for a committee legislation item"""
    committee = get_object_or_404(Committee, code=code)
    legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

    user = request.user
    is_chair = committee.is_chair(user)
    is_member = committee.voting_members.filter(pk=user.pk).exists() or committee.non_voting_members.filter(pk=user.pk).exists()

    # Check if user can view results
    # User can view if: they voted, they are a chair, they are a member, or voting is closed
    user_voted = CommitteeVote.objects.filter(user=user, legislation=legislation).exists()
    can_view = user_voted or is_chair or is_member or legislation.voting_closed

    if not can_view:
        from django.contrib import messages
        from django.shortcuts import redirect
        messages.error(request, "You don't have permission to view these results.")
        return redirect('vote', code=code)

    # Get all votes
    votes = CommitteeVote.objects.filter(legislation=legislation).select_related('user')

    # Calculate results based on vote mode
    anonymous = legislation.anonymous_vote

    if legislation.vote_mode == 'plurality':
        # Plurality voting
        vote_breakdown = {}
        plurality_results = {'results': []}

        for option in (legislation.plurality_options or []):
            option_votes = votes.filter(vote_choice=option)
            vote_breakdown[option] = option_votes.count()

            if not anonymous:
                plurality_results['results'].append({
                    'option': option,
                    'voters': [v.user.name for v in option_votes]
                })

        total_votes = votes.count()

        # Calculate unique voter count for multi-select votes
        unique_voter_count = votes.values('user').distinct().count()

        # Determine winner or tie
        has_tie = legislation.has_plurality_tie() if hasattr(legislation, 'has_plurality_tie') else False
        top_options = legislation.get_top_options_for_runoff() if hasattr(legislation, 'get_top_options_for_runoff') else []

        # Determine winning option(s)
        winning_option = None
        tied_options = []
        if vote_breakdown:
            max_votes = max(vote_breakdown.values()) if vote_breakdown.values() else 0
            if max_votes > 0:
                winners = [opt for opt, count in vote_breakdown.items() if count == max_votes]
                if len(winners) == 1:
                    winning_option = winners[0]
                else:
                    tied_options = winners
                    has_tie = True

        # Check if user can create runoff
        can_create_runoff = (
            legislation.voting_closed and
            legislation.plurality_runoff_enabled and
            not legislation.plurality_is_runoff and
            not legislation.runoff_votes.exists() and
            (is_chair or legislation.posted_by == user) and
            len(top_options) >= 2
        )

        return render(request, 'committee/vote_result.html', {
            'committee': committee,
            'legislation': legislation,
            'votes': votes,
            'vote_breakdown': vote_breakdown,
            'plurality_results': plurality_results,
            'total_votes': total_votes,
            'unique_voter_count': unique_voter_count,
            'plurality_votes_allowed': legislation.plurality_votes_allowed,
            'anonymous': anonymous,
            'is_chair': is_chair,
            'has_tie': has_tie,
            'winning_option': winning_option,
            'tied_options': tied_options,
            'top_options_for_runoff': top_options,
            'can_create_runoff': can_create_runoff,
            'runoff_enabled': legislation.plurality_runoff_enabled,
            'is_runoff': legislation.plurality_is_runoff,
            'parent_vote': legislation.plurality_parent,
        })
    else:
        # Percentage or Piecewise voting (Yes/No/Abstain)
        in_favor = votes.filter(vote_choice='yes')
        against = votes.filter(vote_choice='no')
        abstain = votes.filter(vote_choice='abstain')

        yes_count = in_favor.count()
        no_count = against.count()
        abstain_count = abstain.count()
        total_votes = votes.count()

        # Calculate percentages
        countable_votes = yes_count + no_count
        if countable_votes > 0:
            yes_percentage = round((yes_count / countable_votes) * 100, 1)
        else:
            yes_percentage = 0

        required_percentage = int(legislation.required_percentage)
        passed = legislation.passed if legislation.voting_closed else (yes_percentage >= required_percentage if countable_votes > 0 else False)

        return render(request, 'committee/vote_result.html', {
            'committee': committee,
            'legislation': legislation,
            'votes': votes,
            'in_favor': in_favor,
            'against': against,
            'abstain': abstain,
            'yes_count': yes_count,
            'no_count': no_count,
            'abstain_count': abstain_count,
            'total_votes': total_votes,
            'yes_percentage': yes_percentage,
            'required_percentage': required_percentage,
            'passed': passed,
            'anonymous': anonymous,
            'is_chair': is_chair,
        })
