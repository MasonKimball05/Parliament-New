from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.urls import reverse
from django.db.models import Count, Q
from django.db.models import Prefetch
from src.models.users import member_defer
from ..forms import LegislationDraftForm
from ..models import (
    Legislation, LegislationDraft, Vote, AnnouncementPoll, ParliamentUser,
    MEMBER_DISPLAY_FIELDS, MEMBER_PROFILE_FIELDS,
)
from .legislation_drafts import MY_DRAFTS_LIMIT, _can_publish

#: Ceiling on the "my polls" panel at the bottom of this page. See the comment
#: at its queryset — it is a ceiling on an already-user-scoped list, not a page
#: size, so it is deliberately generous.
MY_POLLS_LIMIT = 100


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

    # Counts for status tabs.
    #
    # v3.17.3: was five separate COUNT round trips over the same table — the
    # pattern v3.17.2 collapsed in passed_legislation.py and did not carry over
    # to its sibling. Conditional aggregation evaluates all five predicates in a
    # single pass.
    #
    # `distinct=True` is load-bearing, not decoration: base_qs joins co_authors
    # (`Q(posted_by=user) | Q(co_authors=user)`) and carries a `.distinct()`,
    # which applies to the row stream and not to an aggregate. Without it, a
    # bill the user both posted and co-authored would be counted twice. The
    # aliases are `n_`-prefixed because an aggregate may not share a name with a
    # model field — `passed` is also a BooleanField.
    _failed_q = (
        (Q(status='failed') | (Q(passed=False) & Q(voting_closed=True)))
        & ~Q(status__in=['passed', 'tabled'])
    )
    _counts = base_qs.aggregate(
        n_all=Count('pk', distinct=True),
        n_active=Count('pk', filter=Q(voting_closed=False), distinct=True),
        n_passed=Count(
            'pk',
            filter=Q(status='passed') | Q(passed=True, voting_closed=True),
            distinct=True,
        ),
        n_failed=Count('pk', filter=_failed_q, distinct=True),
        n_tabled=Count('pk', filter=Q(status='tabled'), distinct=True),
    )
    status_counts = {
        'all': _counts['n_all'],
        'active': _counts['n_active'],
        'passed': _counts['n_passed'],
        'failed': _counts['n_failed'],
        'tabled': _counts['n_tabled'],
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
        .defer(*member_defer('posted_by'))
        .prefetch_related(Prefetch(
            'co_authors',
            queryset=ParliamentUser.objects.only(*MEMBER_DISPLAY_FIELDS),
        ))
    )
    # v3.17.3: this page had no pagination at all — it rendered every bill the
    # user has ever authored or co-authored, each with two reverse() calls, a
    # set_passed() and a full context dict. It was the only legislation page
    # without a page size. Paginating the queryset (not the finished list) also
    # keeps the joins and prefetch above scoped to the 20 rows on screen, and
    # bounds the set_passed() calls in the loop below.
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    legislation_list = list(page_obj.object_list)

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

    # v3.17.3: the template wants two numbers per poll — how many questions and
    # how many responses. It was getting them by prefetching every question and
    # every response object and calling `.count()` on the cached lists, so a
    # poll with 300 answers pulled 300 rows to render "300". Two annotations do
    # it in the same single query, and nothing else on the page touches the
    # related objects. `distinct=True` on both is required: two multi-valued
    # joins in one annotate() multiply each other's rows and inflate BOTH counts
    # without it.
    #
    # v3.17.5: `[:MY_POLLS_LIMIT]`. The expensive half was fixed above, but the
    # queryset still had no ceiling — it returned every poll the user has ever
    # created, and this is a secondary panel at the bottom of a page about
    # something else. Scoped to `created_by=user` so it is small by
    # construction; the cap is a ceiling, not a page size.
    my_polls = list(
        AnnouncementPoll.objects.filter(created_by=user)
        .select_related('announcement')
        .annotate(
            response_total=Count('responses', distinct=True),
            question_total=Count('questions', distinct=True),
        )
        .order_by('-created_at')[:MY_POLLS_LIMIT]
    )

    # v3.19.0 — the author's private drafts.
    #
    # ⚠️ `author=user` is the whole access control for this panel, and it is the
    # reason a draft is a separate model rather than a flag on Legislation: the
    # queryset cannot accidentally widen to somebody else's row, because there is
    # no other row in scope. `select_related('published_legislation')` because
    # the template links published drafts to the bill they became, and a
    # published draft is the common case for anyone who has used this a while.
    #
    # `ready_to_publish()` is evaluated here rather than in the template so the
    # reason a draft cannot be published is available as text next to a disabled
    # button — a greyed-out control with no explanation is the thing people file
    # bugs about.
    my_drafts_qs = (
        LegislationDraft.objects
        .filter(author=user)
        .select_related('published_legislation')
        .order_by('-updated_at')[:MY_DRAFTS_LIMIT]
    )
    my_drafts = []
    unpublished_count = 0
    for draft in my_drafts_qs:
        ready, reason = draft.ready_to_publish()
        if not draft.is_published:
            unpublished_count += 1
        my_drafts.append({
            'draft': draft,
            'ready_to_publish': ready,
            'not_ready_reason': reason,
        })

    return render(request, 'legislation_history.html', {
        'legislation_history': legislation_history,
        'page_obj': page_obj,
        'total_count': paginator.count,
        'status_filter': status_filter,
        'status_counts': status_counts,
        'my_polls': my_polls,
        'my_drafts': my_drafts,
        'draft_count': unpublished_count,
        'draft_form': LegislationDraftForm(),
        'can_publish_drafts': _can_publish(user),
        # Which tab to open on load. The page also remembers the last tab in
        # localStorage; an explicit ?tab= wins over that, which is what makes
        # the post-redirect from the draft views land where the user was.
        'initial_tab': request.GET.get('tab', ''),
    })
