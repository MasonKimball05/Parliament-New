from ..models import *
from django.views.generic.detail import DetailView

class PassedLegislationDetailView(DetailView):
    model = Legislation
    template_name = 'src/legislation_detail.html'
    context_object_name = 'legislation'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        legislation = self.object
        votes = Vote.objects.filter(legislation=legislation)
        total_votes = votes.count()

        if legislation.vote_mode == 'plurality':
            vote_counts = {option: votes.filter(vote_choice=option).count() for option in legislation.plurality_options}
            winner = max(vote_counts, key=vote_counts.get) if vote_counts else None
            context['vote_result'] = {
                'mode': 'plurality',
                'options': vote_counts,
                'winner': winner,
                'total': total_votes
            }
        else:
            yes_votes = votes.filter(vote_choice='yes').count()
            no_votes = votes.filter(vote_choice='no').count()
            abstain_votes = votes.filter(vote_choice='abstain').count()
            yes_pct = (yes_votes / total_votes * 100) if total_votes > 0 else 0
            context['vote_result'] = {
                'mode': 'percentage',
                'yes': yes_votes,
                'no': no_votes,
                'abstain': abstain_votes,
                'yes_percentage': "{:.0f}%".format(yes_pct),
                'required_percentage': legislation.required_percentage,
                'total': total_votes
            }

        return context