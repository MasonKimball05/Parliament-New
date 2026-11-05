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
from .permissions import (
    _period_is_locked, applicant_ineligibility, can_manage_period, can_view_applications,
    has_live_application, on_slating_committee,
)

#: Who can be marked present (src/view/slating/attendance.py), so who can vote.
VOTING_STATUSES = ('Active', 'Inactive')


def can_vote_in_slating(user):
    """``can_vote`` (members, chairs, officers; no pledges or advisors) and
    active or inactive, i.e. someone the attendance list can mark present."""
    return bool(user.can_vote and user.member_status in VOTING_STATUSES)


def may_apply(period, user):
    """
    Whether the dashboards offer Apply Now: applications open, the applicant
    rules (``applicant_ineligibility``: active, not an advisor; pledges may
    apply), not on the slating committee, and not already applied.
    """
    return bool(period.can_apply() and applicant_ineligibility(user) is None
                and not on_slating_committee(user, period) and not has_live_application(user, period))


def has_voted_this_round(period, user):
    """This round: the slate ballot cast, or every position on the ballot voted on."""
    from src.slating_ballots import has_slate_ballot, individual_ballot, primary_slate
    if period.vote_type != 'individual':
        return has_slate_ballot(period, user)
    slate = primary_slate(period)
    if slate is None:
        return False
    _, ballot, voted, _ = individual_ballot(period, slate, user)
    return bool(ballot) and all(s.position.id in voted for s in ballot)


def sits_out_everything(period, user):
    """v4.4.5: a candidate with nothing to vote on this round (their whole
    slate, or every position on this round's ballot is their own)."""
    from src.slating_ballots import (
        has_slate_ballot, individual_ballot, primary_slate, sat_out_states, sits_out_slate,
    )
    slate = primary_slate(period)
    if slate is None:
        return False
    if period.vote_type != 'individual':
        return sits_out_slate(period, slate, user) and not has_slate_ballot(period, user)
    _, ballot, _, _ = individual_ballot(period, slate, user)
    return not ballot and bool(sat_out_states(period, slate, user))


def managed_by(period):
    """The name of who runs a period: its slating manager, else its committee's admin."""
    if period.slating_manager_id:
        return period.slating_manager.name
    committee = period.slating_committee if period.slating_committee_id else None
    if committee is not None and committee.admin_id:
        return committee.admin.name
    return ''


def period_actions(period, user):
    """
    What ``user`` can do with ``period`` from the Slating hub — shared by the
    web dashboard and the app's (``src/mobile/views_slating.py``) so they
    offer the same buttons:

    * ``can_apply`` — Apply Now (``may_apply``).
    * ``can_vote`` / ``has_voted`` / ``sits_out`` — Cast Vote, "✓ You voted",
      or nothing to vote on; only for people who can vote.
    * ``can_manage`` — "Manage election →" (the slating manager, committee
      admin/chairs; a site admin while the period isn't locked).
    * ``can_review`` — the committee's read access (applications, interviews).
    * ``locked_out`` / ``managed_by`` — a site admin kept out of a locked
      period sees who runs it instead.
    """
    open_now = period.can_vote()
    voter = can_vote_in_slating(user)
    has_voted = bool(open_now and voter and has_voted_this_round(period, user))
    sits_out = bool(open_now and voter and not has_voted and sits_out_everything(period, user))
    can_manage = can_manage_period(user, period)
    can_review = can_view_applications(user, period)
    locked_out = bool(user.is_admin and not can_review and _period_is_locked(period))
    return {
        'can_apply': may_apply(period, user),
        'voting_open': open_now,
        'can_vote': bool(open_now and voter and not has_voted and not sits_out),
        'has_voted': has_voted,
        'sits_out': sits_out,
        'can_manage': can_manage,
        'can_review': can_review,
        'locked_out': locked_out,
        'managed_by': managed_by(period) if locked_out else '',
    }


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

    # 11-05-26: what this member can do with each period (the slating manager
    # gets "Manage election", a locked-out admin sees who runs it, a member
    # who voted sees "You voted"). Was committee chair/member only, and every
    # period for a site admin, even ones locked away from them.
    active_periods = list(active_periods.select_related('slating_manager', 'slating_committee__admin'))
    period_rows = [{'period': p, **period_actions(p, user)} for p in active_periods]
    committee_periods = [r['period'] for r in period_rows if r['can_review']]
    manage_periods = [r['period'] for r in period_rows if r['can_manage']]
    is_committee_member = bool(committee_periods)
    is_committee_chair = bool(manage_periods)

    # Pending reviews (for committee members)
    pending_reviews = []
    if committee_periods:
        period_ids = [p.id for p in committee_periods]
        pending_reviews = SlatingApplication.objects.filter(
            period_id__in=period_ids,
            status='submitted'
        ).select_related('applicant', 'period').defer(*member_defer('applicant')).order_by('-submitted_at')[:10]

    voted_period_ids = [r['period'].id for r in period_rows if r['has_voted']]

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
        'period_rows': period_rows,
        # The sidebar's Quick Links / Administration (they read these names).
        'committee_period': committee_periods[0] if committee_periods else None,
        'is_chair_or_admin': is_committee_chair,
        'admin_period': manage_periods[0] if manage_periods else None,
        'all_periods': period_rows,
        'published_results': recent_results,
        'pending_reviews': pending_reviews,
        'recent_results': recent_results,
    }

    return render(request, 'slating/dashboard.html', context)
