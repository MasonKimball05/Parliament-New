from django.shortcuts import render, get_object_or_404
from ..models import *

def legislation_detail(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)
    votes = Vote.objects.filter(legislation=legislation)

    if legislation.vote_mode == 'plurality':
        vote_result = {
            'mode': 'plurality',
            'options': {option: votes.filter(vote_choice=option).count() for option in legislation.plurality_options},
            'total': votes.count()
        }
    else:
        yes_votes = votes.filter(vote_choice='yes').count()
        no_votes = votes.filter(vote_choice='no').count()
        abstain_votes = votes.filter(vote_choice='abstain').count()
        total = votes.count()
        yes_pct = (yes_votes / total * 100) if total > 0 else 0
        vote_result = {
            'mode': 'percentage',
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
            'yes_percentage': "{:.0f}%".format(yes_pct),
            'required_percentage': legislation.required_percentage,
            'total': total
        }

    return render(request, 'src/legislation_detail.html', {
        'legislation': legislation,
        'vote_result': vote_result
    })
