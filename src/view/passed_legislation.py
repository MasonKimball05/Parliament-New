from django.contrib.auth.decorators import login_required
from ..decorators import *
from ..models import *
from django.shortcuts import render
from django.views.generic import DetailView
import pytz
from datetime import timedelta

@login_required
@log_function_call
def passed_legislation(request):
    closed_legislation = Legislation.objects.filter(voting_closed=True)
    passed = []
    passed_legs = Legislation.objects.filter(passed=True)
    print("Passed Legislation:", passed_legs)

    for leg in closed_legislation:
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        abstain = votes.filter(vote_choice='abstain').count()
        total_non_abstain = yes + no

        if total_non_abstain == 0:
            continue

        vote_passed = False
        if leg.vote_mode == 'peacewise':
            vote_passed = yes >= leg.required_yes_votes
        else:
            yes_pct = (yes / total_non_abstain) * 100
            required_pct = int(leg.required_percentage)
            vote_passed = yes_pct >= required_pct

        vote_breakdown = {
            'yes': yes,
            'no': no,
            'abstain': abstain
        }
        if leg.vote_mode == 'plurality':
            winner = max(vote_breakdown, key=vote_breakdown.get)
        else:
            winner = None


        # Determine time range for attendance window
        local_tz = pytz.timezone("America/Chicago")
        vote_end = leg.voting_ended_at or leg.available_at
        vote_start = vote_end - timedelta(hours=3)

        # Convert to local time and back to UTC to simulate attendance in UTC-6 window
        vote_start_local = vote_start.astimezone(local_tz)
        vote_end_local = vote_end.astimezone(local_tz)

        vote_start_utc = vote_start_local.astimezone(pytz.UTC)
        vote_end_utc = vote_end_local.astimezone(pytz.UTC)


        # Only get the latest attendance record per user in the window
        present_members = Attendance.objects.filter(
            present=True,
            created_at__range=(vote_start_utc, vote_end_utc)
        ).order_by('user_id', '-created_at').distinct('user_id').select_related('user')

        passed.append({
            'legislation': leg,
            'yes': yes,
            'no': no,
            'abstain': abstain,
            'yes_pct': round(yes_pct, 2),
            'no_pct': round((no / total_non_abstain) * 100, 2),
            'required_pct': required_pct,
            'required_yes_votes': getattr(leg, 'required_yes_votes', None),
            'vote_mode': leg.vote_mode,
            'vote_passed': vote_passed,
            'present_members': present_members,
            'document_url': leg.document.url if leg.document else None,
            'vote_breakdown': vote_breakdown,
            'winner': winner,
        })

        logger.info(f"{leg.title} present members: {[a.user.name for a in present_members]}")

        print("Present members for:", leg.title)
        for pm in present_members:
            print(f"- {pm.user.name} @ {pm.created_at}")

    return render(request, 'passed_legislation.html', {'passed_legislation': passed})


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