from ..decorators import log_function_call
from ..models import Legislation
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from src import chapter_votes
from src.feature_flag_decorators import require_feature_flag
from src.models.users import member_defer

@login_required
@require_feature_flag('legislation_voting')
@require_POST
@log_function_call
def end_vote(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # Allow the uploader OR admins to end the vote
    if not chapter_votes.can_run_vote(request.user, legislation):
        return HttpResponseForbidden("Only the uploader or an admin can end the vote.")

    # v4.4.2: a vote ends once. A second POST (a double tap, a resent form)
    # used to close it again and send every voter the result again.
    if legislation.voting_closed:
        messages.info(request, 'Voting on this legislation has already ended.')
        return redirect('legislation_detail', legislation_id=legislation.id)

    # v4.4.2: the close, outcome, broadcast, log and notifications are
    # src/chapter_votes.py, shared with the app.
    outcome = chapter_votes.end_vote(legislation, request.user, request)
    votes = outcome['votes']
    vote_summary = outcome['vote_summary']
    vote_passed = outcome['passed']
    total_votes = outcome['total_votes']
    yes_votes = outcome['yes_votes']
    yes_percentage = outcome['yes_percentage']
    required_pct = outcome['required_pct']
    vote_breakdown = outcome['vote_breakdown']
    winner = outcome['winner']

    context = {
        'legislation': legislation,
        'summary': vote_summary,
        'anonymous': legislation.anonymous_vote,
        'remove_abstain': not legislation.allow_abstain,
        'passed': vote_passed,
        'total_votes': total_votes,
        'yes_votes': yes_votes,
        'yes_percentage': f"{yes_percentage:.0f}%" if yes_percentage is not None else "N/A",
        'required_percentage': required_pct if required_pct is not None else 'N/A',
        'vote_breakdown': vote_breakdown,
        'winner': winner,
    }

    if not legislation.anonymous_vote:
        context['in_favor'] = votes.filter(vote_choice='yes')
        context['against'] = votes.filter(vote_choice='no')
        context['abstain'] = votes.filter(vote_choice='abstain')

    #legislation.set_passed()

    if legislation.vote_mode == 'plurality':
        # Get sorted results for display
        sorted_results = legislation.get_plurality_results()
        context['plurality_results'] = {
            'results': [
                {
                    'option': r['option'],
                    'count': r['count'],
                    'voters': [v.user.name for v in votes.filter(vote_choice=r['option']).select_related('user').defer(*member_defer('user'))]
                }
                for r in sorted_results
            ]
        }

        # Per-option horizontal bars (v3.15.9): pie slices are hard to compare
        # in close races. `width` is relative to the leader (leader = full bar)
        # so small differences stay visible; `pct` is share of all choice
        # selections (multi-vote plurality casts several selections per ballot).
        _lead = sorted_results[0]['count'] if sorted_results else 0
        _denom = sum(r['count'] for r in sorted_results)
        context['plurality_bars'] = [
            {
                'option': r['option'],
                'count': r['count'],
                'pct': round(r['count'] * 100.0 / _denom, 1) if _denom else 0,
                'width': round(r['count'] * 100.0 / _lead, 1) if _lead else 0,
                'is_winner': winner is not None and r['option'] == winner,
            }
            for r in sorted_results
        ]

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
@require_feature_flag('legislation_voting')
@require_POST
@log_function_call
def create_runoff(request, legislation_id):
    """Create a runoff vote from a completed plurality vote."""
    original = get_object_or_404(Legislation, id=legislation_id)

    # Verify permissions
    if not chapter_votes.can_run_vote(request.user, original):
        return HttpResponseForbidden("Only the uploader or an admin can create a runoff.")

    # v4.4.2: the checks, the new vote and its log are src/chapter_votes.py.
    try:
        runoff, top_options = chapter_votes.create_runoff(original, request.user, request)
    except chapter_votes.ChapterVoteError as exc:
        messages.error(request, exc.message)
        return redirect('vote')
    messages.success(request, f"Runoff vote created with top {len(top_options)} options: {', '.join(top_options)}")
    return redirect('vote')