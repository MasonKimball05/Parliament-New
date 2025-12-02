from ..decorators import *
from ..models import *
from django.db.models import Count
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

@login_required
@log_function_call
def end_vote(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if request.user != legislation.posted_by:
        return HttpResponseForbidden("Only the uploader can end the vote.")

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
        vote_passed = True if most_voted else False
        winner = most_voted

    # Update status based on vote outcome
    if vote_passed:
        legislation.status = 'passed'
    else:
        legislation.status = 'removed'
    legislation.save()

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
        context['plurality_results'] = {
            'results': [
                {
                    'option': option,
                    'count': vote_breakdown.get(option, 0),
                    'voters': [v.user.name for v in votes.filter(vote_choice=option).select_related('user')]
                }
                for option in legislation.plurality_options
            ]
        }

    return render(request, 'vote_result.html', context)