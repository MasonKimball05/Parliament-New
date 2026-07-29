from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.db.models import Count, Q
from django.db.models import Prefetch
from ..models import (
    Legislation, Vote, AnnouncementPoll, ParliamentUser,
    MEMBER_DISPLAY_FIELDS, MEMBER_PROFILE_FIELDS,
)


@login_required
def view_legislation_history(request):
    user = request.user
    status_filter = request.GET.get('status', 'all')

    # All legislation submitted by or co-authored by the user
    base_qs = Legislation.objects.filter(
        Q(posted_by=user) | Q(co_authors=user)
    ).distinct().order_by('-available_at')

    # Apply status filter
    if status_filter == 'active':
        queryset = base_qs.filter(voting_closed=False)
    elif status_filter == 'passed':
        queryset = base_qs.filter(Q(status='passed') | Q(passed=True, voting_closed=True))
    elif status_filter == 'failed':
        queryset = base_qs.filter(
            Q(status='failed') | (Q(passed=False) & Q(voting_closed=True))
        ).exclude(status__in=['passed', 'tabled'])
    elif status_filter == 'tabled':
        queryset = base_qs.filter(status='tabled')
    else:
        queryset = base_qs

    # Counts for status tabs
    status_counts = {
        'all': base_qs.count(),
        'active': base_qs.filter(voting_closed=False).count(),
        'passed': base_qs.filter(
            Q(status='passed') | Q(passed=True, voting_closed=True)
        ).count(),
        'failed': base_qs.filter(
            Q(status='failed') | (Q(passed=False) & Q(voting_closed=True))
        ).exclude(status__in=['passed', 'tabled']).count(),
        'tabled': base_qs.filter(status='tabled').count(),
    }

    # v3.17.1 perf. This page was ~7 queries per row of legislation:
    #   * one implicit fetch of `posted_by` per row (the reported N+1 — user_id
    #     is ParliamentUser's primary key, so each row re-fetched the same
    #     author by pk because nothing was select_related),
    #   * two more per row for `co_authors.all`, which the template iterates
    #     twice,
    #   * three .count() calls per row for yes/no/abstain, plus one per option
    #     on plurality bills,
    #   * and two to three more inside set_passed(), which then wrote the row.
    #
    # Now: one query for the page of legislation (authors joined, co-authors
    # prefetched) and one aggregate for every vote across all of them.
    # v3.17.2: narrowed — see the same change in passed_legislation.py.
    queryset = (
        queryset
        .select_related('posted_by')
        .defer(*(f'posted_by__{f}' for f in MEMBER_PROFILE_FIELDS))
        .prefetch_related(Prefetch(
            'co_authors',
            queryset=ParliamentUser.objects.only(*MEMBER_DISPLAY_FIELDS),
        ))
    )
    legislation_list = list(queryset)

    tally = {}
    for row in (
        Vote.objects
        .filter(legislation__in=legislation_list)
        .values('legislation_id', 'vote_choice')
        .annotate(n=Count('id'))
    ):
        tally.setdefault(row['legislation_id'], {})[row['vote_choice']] = row['n']

    legislation_history = []

    for leg in legislation_list:
        counts = tally.get(leg.pk, {})
        yes = counts.get('yes', 0)
        no = counts.get('no', 0)
        abstain = counts.get('abstain', 0)

        # Use historical counts if available
        if leg.historical_yes_votes is not None:
            yes = leg.historical_yes_votes
        if leg.historical_no_votes is not None:
            no = leg.historical_no_votes
        if leg.historical_abstain_votes is not None:
            abstain = leg.historical_abstain_votes

        total_non_abstain = yes + no
        total_votes = yes + no + abstain

        # Update passed status for closed votes.
        # v3.17.1: `counts` is the aggregate we already have, so this issues no
        # queries; and set_passed now only writes when the value actually
        # changed, so viewing this page no longer rewrites every closed bill.
        if leg.voting_closed:
            try:
                leg.set_passed(counts=counts)
            except Exception:
                pass

        # Vote mode specific calculations
        yes_pct_num = 0
        yes_pct_display = '0%'
        no_pct_num = 0
        vote_breakdown = None
        winner = None
        required_pct = None
        required_yes = None

        if leg.vote_mode == 'plurality' and leg.plurality_options:
            # v3.17.1: read from the shared aggregate rather than one COUNT per option.
            vote_breakdown = {option: counts.get(option, 0) for option in leg.plurality_options}
            winner = max(vote_breakdown, key=vote_breakdown.get) if vote_breakdown else None

        elif leg.vote_mode == 'piecewise':
            required_yes = (
                getattr(leg, 'required_yes_votes', None)
                or getattr(leg, 'required_number', None)
                or 0
            )

        else:  # percentage
            required_pct = int(leg.required_percentage) if leg.required_percentage else 51
            if total_non_abstain > 0:
                yes_pct_num = round((yes / total_non_abstain) * 100, 1)
                no_pct_num = round((no / total_non_abstain) * 100, 1)
                yes_pct_display = '{:.0f}%'.format(yes_pct_num)

        legislation_history.append({
            'legislation': leg,
            'yes': yes,
            'no': no,
            'abstain': abstain,
            'total_votes': total_votes,
            'yes_pct_num': yes_pct_num,
            'yes_pct_display': yes_pct_display,
            'no_pct_num': no_pct_num,
            'required_pct': required_pct,
            'required_yes': required_yes,
            'vote_mode': leg.vote_mode,
            'vote_breakdown': vote_breakdown,
            'winner': winner,
            'passed': leg.passed,
            'voting_closed': leg.voting_closed,
            'is_active': leg.is_available() and not leg.voting_closed,
            'document_url': leg.document.url if leg.document else None,
            'document_viewer_url': reverse('view_document', args=[leg.id]) if leg.document else None,
            'detail_url': reverse('legislation_detail', args=[leg.id]),
        })

    my_polls = list(
        AnnouncementPoll.objects.filter(created_by=user)
        .select_related('announcement')
        .prefetch_related('questions', 'responses')
        .order_by('-created_at')
    )

    return render(request, 'legislation_history.html', {
        'legislation_history': legislation_history,
        'status_filter': status_filter,
        'status_counts': status_counts,
        'my_polls': my_polls,
    })
