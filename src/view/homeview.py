from django.views.generic import TemplateView
from ..models import *

class HomeView(TemplateView):
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Fetch the last two legislations with votes
        recent_legislation = Legislation.objects.filter(voting_closed=True).order_by('-available_at')[:2]
        recent_votes = []
        for leg in recent_legislation:
            yes_votes = Vote.objects.filter(legislation=leg, vote_choice='yes').count()
            no_votes = Vote.objects.filter(legislation=leg, vote_choice='no').count()
            abstain_votes = Vote.objects.filter(legislation=leg, vote_choice='abstain').count()
            recent_votes.append({
                'title': leg.title,
                'yes': yes_votes,
                'no': no_votes,
                'abstain': abstain_votes,
                'id': leg.id
            })
        context['recent_votes'] = recent_votes
        return context