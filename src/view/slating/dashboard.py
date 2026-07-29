"""
Slating Dashboard View

Main hub for the slating system showing:
- Active periods where user can apply
- User's applications
- For committee members: pending reviews
- For admins: management options
"""

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from src.models import SlatingPeriod, SlatingApplication
from .period_setup import check_and_auto_transition_status
from src.models.users import member_defer


@login_required
def slating_dashboard(request):
    """
    Main slating hub. Shows different content based on user role.
    """
    user = request.user
    now = timezone.now()

    # Get active periods (not archived)
    active_periods = SlatingPeriod.objects.exclude(
        status='archived'
    ).order_by('-created_at')

    # Check for automatic status transitions on all active periods
    for period in active_periods:
        check_and_auto_transition_status(period)

    # Periods where nominations are open (user can apply)
    open_periods = active_periods.filter(status='nominations_open')

    # Periods where voting is open
    voting_periods = active_periods.filter(status='voting_open')

    # User's applications
    my_applications = SlatingApplication.objects.filter(
        applicant=user
    ).select_related('period').order_by('-created_at')

    # Check if user has pending applications (draft)
    draft_applications = my_applications.filter(status='draft')

    # Check if user is on any slating committee
    is_committee_member = False
    is_committee_chair = False
    committee_periods = []

    if user.is_admin:
        is_committee_member = True
        is_committee_chair = True
        committee_periods = list(active_periods)
    else:
        for period in active_periods:
            if period.slating_committee:
                committee = period.slating_committee
                if committee.is_chair(user):
                    is_committee_member = True
                    is_committee_chair = True
                    committee_periods.append(period)
                elif committee.is_member(user):
                    is_committee_member = True
                    committee_periods.append(period)

    # Pending reviews (for committee members)
    pending_reviews = []
    if is_committee_member and committee_periods:
        period_ids = [p.id for p in committee_periods]
        pending_reviews = SlatingApplication.objects.filter(
            period_id__in=period_ids,
            status='submitted'
        ).select_related('applicant', 'period').defer(*member_defer('applicant')).order_by('-submitted_at')[:10]

    # Has the user already voted in any open voting period?
    from src.models import SlatingBallot
    voted_period_ids = SlatingBallot.objects.filter(
        voter=user,
        period__in=voting_periods
    ).values_list('period_id', flat=True).distinct()

    # Recent results (published in last 30 days)
    recent_results = SlatingPeriod.objects.filter(
        status='results_published',
        results_publish_at__gte=now - timezone.timedelta(days=30)
    ).order_by('-results_publish_at')[:5]

    context = {
        'active_periods': active_periods,
        'open_periods': open_periods,
        'voting_periods': voting_periods,
        'voted_period_ids': list(voted_period_ids),
        'my_applications': my_applications,
        'draft_applications': draft_applications,
        'is_committee_member': is_committee_member,
        'is_committee_chair': is_committee_chair,
        'committee_periods': committee_periods,
        'pending_reviews': pending_reviews,
        'recent_results': recent_results,
    }

    return render(request, 'slating/dashboard.html', context)
