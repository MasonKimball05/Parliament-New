from ..decorators import *
from ..models import *
from django.db.models import Count
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.urls import reverse

@login_required
@log_function_call
def home(request):
    # Get the last two passed legislations
    print(f"🔐 User: {request.user} | Authenticated: {request.user.is_authenticated}")
    logger.info(f"User: {request.user} | Authenticated: {request.user.is_authenticated} | IP: {request.META.get('REMOTE_ADDR')} | Page accessed: home")
    recently_passed_legislation = Legislation.objects.annotate(
        total_votes=Count('vote'),
        yes_votes=Count('vote', filter=Q(vote__vote_choice='yes'))
    ).filter(
        voting_closed=True,
        status='passed'
    ).order_by('-available_at')[:2]  # Change field name as per your model

    # Preparing data to display
    legislation_previews = [
        {
            'title': leg.title,
            'yes_percentage': "{:.0%}".format(leg.yes_votes / leg.total_votes) if leg.total_votes > 0 else "0%",
            'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk})
        } for leg in recently_passed_legislation
    ]

    context = {
        'user': request.user,
        'legislation_previews': legislation_previews,
    }

    return render(request, 'home.html', context)