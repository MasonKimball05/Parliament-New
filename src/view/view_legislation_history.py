from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from ..models import *

@login_required
def view_legislation_history(request):
    user = request.user

    # Fetch all legislation submitted by the logged-in user (both past and present)
    user_legislation = Legislation.objects.filter(posted_by=user).order_by('-available_at')

    legislation_history = []

    for leg in user_legislation:
        votes = Vote.objects.filter(legislation=leg)
        yes_votes = votes.filter(vote_choice='yes').count()
        no_votes = votes.filter(vote_choice='no').count()
        abstain_votes = votes.filter(vote_choice='abstain').count()
        total_votes = votes.count()

        # Calculate the yes percentage
        yes_percentage = (yes_votes / total_votes) * 100 if total_votes > 0 else 0

        # Update the passed status based on votes
        if leg.voting_closed:
            leg.set_passed()
        passed = leg.passed

        is_legislation_active = leg.is_available() and not leg.voting_closed

        # Adding legislation history with voting results for closed ones
        legislation_history.append({
            'legislation': leg,
            'yes_votes': yes_votes,
            'no_votes': no_votes,
            'abstain_votes': abstain_votes,
            'total_votes': total_votes,
            'yes_pct': round(yes_percentage, 2),
            'no_pct': round((no_votes / total_votes) * 100, 2) if total_votes > 0 else 0,
            'abstain_pct': round((abstain_votes / total_votes) * 100, 2) if total_votes > 0 else 0,
            'is_active': is_legislation_active,
            'voting_closed': leg.voting_closed,
            'available_at': leg.available_at,
            'voting_ended_at': leg.voting_ended_at,  # assuming you store the end time
            'anonymous_vote': leg.anonymous_vote,
            'allow_abstain': leg.allow_abstain,
            'description': leg.description,
            'title': leg.title,
            'document_url': leg.document.url if leg.document else None,
            'legislation_id': leg.id,
            'passed': passed,
        })

    return render(request, 'legislation_history.html', {'legislation_history': legislation_history})
