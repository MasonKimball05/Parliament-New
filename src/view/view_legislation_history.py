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


#: Rows per page of the legislation list (v3.17.3). The app's list pages the
#: same way (src/mobile/views_legislation_history.py).
PAGE_SIZE = 20

#: The status tabs, in the page's order. Anything else means 'all'.
STATUS_FILTERS = ('all', 'active', 'passed', 'failed', 'tabled')

# v4.4.0: the querying below was the body of view_legislation_history; it is now
# a set of functions so the app's My Work screen
# (src/mobile/views_legislation_history.py) lists exactly what this page does —
# the same "posted or co-authored" scope, the same status predicates and counts,
# the same tallies. The view calls them in the order it always ran them.


def authored_legislation(user):
    """Every bill ``user`` posted or co-authored, newest first (the whole scope of this page)."""
    return Legislation.objects.filter(
        Q(posted_by=user) | Q(co_authors=user)
    ).distinct().order_by('-available_at')


def filter_status(base_qs, status_filter):
    """``base_qs`` narrowed to one status tab (``STATUS_FILTERS``; anything else is 'all')."""
    if status_filter == 'active':
        return base_qs.filter(voting_closed=False)
    if status_filter == 'passed':
        return base_qs.filter(Q(status='passed') | Q(passed=True, voting_closed=True))
    if status_filter == 'failed':
        return base_qs.filter(
            Q(status='failed') | (Q(passed=False) & Q(voting_closed=True))
        ).exclude(status__in=['passed', 'tabled'])
    if status_filter == 'tabled':
        return base_qs.filter(status='tabled')
    return base_qs


def status_counts(base_qs):
    """How many bills are under each status tab: ``{'all': n, 'active': n, ...}``."""
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
    return {
        'all': _counts['n_all'],
        'active': _counts['n_active'],
        'passed': _counts['n_passed'],
        'failed': _counts['n_failed'],
        'tabled': _counts['n_tabled'],
    }


def history_page(queryset, page_number, per_page=PAGE_SIZE):
    """
    ``(paginator, page_obj, legislation_list)`` for one page of ``queryset``,
    with authors joined and co-authors prefetched.
    """
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
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(page_number)
    legislation_list = list(page_obj.object_list)
    return paginator, page_obj, legislation_list


def history_rows(legislation_list):
    """
    One dict per bill in ``legislation_list``: the vote tally (historical
    counts win when an officer entered them), the result figures for its vote
    mode, and its links. What the page's rows render.
    """
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

        # v3.29.18: this used to call `leg.set_passed(counts=counts)` here on
        # every GET. `counts` is the raw real-Vote-row aggregate captured
        # BEFORE the historical-vote overlay above (lines ~131-136), so for
        # any bill closed via the officer "mark as voted" manual-entry tool
        # (no real Vote rows, only historical_yes/no/abstain_votes) `counts`
        # was empty — set_passed()'s percentage branch then divided by a
        # zero total and forced `passed = False`, and PERSISTED it, flipping
        # a correctly-passed bill to failed purely from viewing this page.
        # That's why the top status badge (reads `leg.status`, untouched)
        # and the vote-result bar (reads `leg.passed`, just corrupted) could
        # show "Passed" and "Failed" on the same row.
        #
        # `leg.passed` is already set correctly at every real closing path —
        # `auto_close_votes.py` calls `set_passed()` itself the moment it
        # closes a vote, and `end_vote.py`'s "End Voting Now" sets `passed`
        # directly — so a render loop has no business recomputing it at all.
        # This page now trusts the stored value, same as its sibling
        # `passed_legislation.py` already does.

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
    return legislation_history


def my_polls(user):
    """The polls ``user`` created, newest first, with question and response totals."""
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
    return list(
        AnnouncementPoll.objects.filter(created_by=user)
        .select_related('announcement')
        .annotate(
            response_total=Count('responses', distinct=True),
            question_total=Count('questions', distinct=True),
        )
        .order_by('-created_at')[:MY_POLLS_LIMIT]
    )


def my_drafts(user):
    """
    ``(drafts, unpublished_count)``: the author's private drafts, each with
    whether it is ready to publish and, if not, why.
    """
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
    rows = []
    unpublished_count = 0
    for draft in my_drafts_qs:
        ready, reason = draft.ready_to_publish()
        if not draft.is_published:
            unpublished_count += 1
        rows.append({
            'draft': draft,
            'ready_to_publish': ready,
            'not_ready_reason': reason,
        })
    return rows, unpublished_count


@login_required
def view_legislation_history(request):
    user = request.user
    status_filter = request.GET.get('status', 'all')

    # All legislation submitted by or co-authored by the user
    base_qs = authored_legislation(user)
    queryset = filter_status(base_qs, status_filter)
    counts = status_counts(base_qs)
    paginator, page_obj, legislation_list = history_page(queryset, request.GET.get('page'))
    legislation_history = history_rows(legislation_list)
    polls = my_polls(user)
    drafts, unpublished_count = my_drafts(user)

    return render(request, 'legislation_history.html', {
        'legislation_history': legislation_history,
        'page_obj': page_obj,
        'total_count': paginator.count,
        'status_filter': status_filter,
        'status_counts': counts,
        'my_polls': polls,
        'my_drafts': drafts,
        'draft_count': unpublished_count,
        'draft_form': LegislationDraftForm(),
        'can_publish_drafts': _can_publish(user),
        # Which tab to open on load. The page also remembers the last tab in
        # localStorage; an explicit ?tab= wins over that, which is what makes
        # the post-redirect from the draft views land where the user was.
        'initial_tab': request.GET.get('tab', ''),
    })
