from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import DetailView
from django.views.decorators.http import require_http_methods
from django.urls import reverse
import logging
from ..decorators import log_function_call
from django.db.models import Prefetch
from ..models import (
    Legislation, Vote, Attendance, ParliamentUser,
    MEMBER_DISPLAY_FIELDS, MEMBER_PROFILE_FIELDS,
)
from src.feature_flag_decorators import require_page_enabled

logger = logging.getLogger(__name__)
from datetime import timedelta
from src.models.users import member_defer


# v3.17.1 — see MEMBER_DISPLAY_FIELDS in src/models/users.py. ParliamentUser is a
# wide table carrying the whole member profile; a page that only prints names has
# no business selecting the bio and five JSON columns for every joined row.
ATTENDANCE_DISPLAY_FIELDS = (
    'id', 'user_id', 'created_at', 'status',
) + tuple(f'user__{name}' for name in MEMBER_DISPLAY_FIELDS)


# Statuses the list always shows, whether or not any votes were cast. Used in
# two places that must agree: the SQL KEEP filter and the loop's invariant
# check. They were separate literals until v3.17.3, when paginating the
# queryset made a disagreement between them visible as short pages.
_ALWAYS_SHOWN_STATUSES = ['tabled', 'pending', 'active', 'passed', 'failed']


def _present_members_in_window(vote_start, vote_end):
    """
    Latest present/late attendance row per user within the window.

    v3.13.3: replaces `.distinct('user_id')` (DISTINCT ON — postgres-only,
    broke the page on sqlite dev) with a Python dedupe; row counts here are
    tiny (≤ chapter size per meeting). Status-based (was present=True): the
    legacy bool is False for 'late' members, who were in the room and voted.
    """
    rows = (
        Attendance.objects
        .filter(status__in=('present', 'late'),
                created_at__range=(vote_start, vote_end))
        .order_by('user_id', '-created_at')
        .select_related('user').defer(*member_defer('user'))
    )
    return _dedupe_latest_per_user(rows)


def _dedupe_latest_per_user(rows):
    """
    Keep the newest attendance row per user.

    Split out in v3.17.1 so the list view can batch one attendance fetch for the
    whole page and slice it per legislation, instead of calling
    `_present_members_in_window` once per row. Expects rows already ordered by
    (user_id, -created_at) — which both call sites guarantee.
    """
    seen, latest = set(), []
    for att in rows:
        if att.user_id not in seen:
            seen.add(att.user_id)
            latest.append(att)
    return latest

@login_required
@require_page_enabled('passed_legislation')
@log_function_call
def passed_legislation(request):
    # Get filter from query params (default to 'all')
    status_filter = request.GET.get('status', 'all')
    now = timezone.now()

    # Base queryset - all non-removed legislation
    all_legislation = Legislation.objects.filter(is_active=True).exclude(status='removed')

    # Apply status filter
    # v3.13.3: most legislation has voting_starts_at=NULL (the vote-page
    # upload and committee push don't set it — voting then starts at
    # available_at), and NULL fails both __gt and __lte, so those items were
    # invisible to the Pending AND Active tabs. Each branch now falls back to
    # available_at when voting_starts_at is NULL.
    # The pending tab only shows not-yet-available legislation to its author —
    # matching the vote page, which promises scheduled legislation is "not yet
    # visible to others" (previously any member could preview scheduled items
    # here).
    _pending_q = Q(status='pending') | (
        Q(voting_closed=False) & (
            Q(voting_starts_at__gt=now) |
            Q(voting_starts_at__isnull=True, available_at__gt=now) |
            # manual-open mode: voting waits for the author (v3.13.3)
            Q(voting_starts_at__isnull=True, voting_manual_open=True)
        ) & (Q(available_at__lte=now) | Q(posted_by=request.user))
    )
    _active_q = Q(status='active') | (
        Q(voting_closed=False) & (
            Q(voting_starts_at__lte=now) |
            Q(voting_starts_at__isnull=True, available_at__lte=now,
              voting_manual_open=False)
        )
    )

    personal_ballots = None
    if status_filter == 'personal':
        # v3.14.0: Personal tab — your own ballots, results, and receipts.
        # Receipts are stateless (regenerated on demand) and verifiable for
        # RECEIPT_MAX_AGE_DAYS; older ballots show an expired notice instead.
        from src.utils.vote_receipts import make_receipt, RECEIPT_MAX_AGE_DAYS
        my_votes = (Vote.objects.filter(user=request.user)
                    .select_related('legislation')
                    .order_by('-id'))
        _grouped = {}
        for v in my_votes:
            _grouped.setdefault(v.legislation_id, []).append(v)
        cutoff = now - timedelta(days=RECEIPT_MAX_AGE_DAYS)
        personal_ballots = []
        for rows in _grouped.values():
            leg = rows[0].legislation
            cast_at = rows[0].cast_at
            fresh = bool(cast_at and cast_at >= cutoff)
            personal_ballots.append({
                'legislation': leg,
                'choices': [r.vote_choice for r in rows],
                'cast_at': cast_at,
                'receipt': make_receipt(request.user, leg, rows, cast_at=cast_at) if fresh else None,
                'receipt_expired': bool(cast_at and cast_at < cutoff),
            })
        personal_ballots.sort(
            key=lambda b: b['cast_at'] or now - timedelta(days=3650), reverse=True)
        queryset = all_legislation.none()
    elif status_filter == 'pending':
        # Pending: voting hasn't started yet
        queryset = all_legislation.filter(_pending_q).order_by('-available_at')
    elif status_filter == 'active':
        # Active: voting is open
        queryset = all_legislation.filter(_active_q).exclude(
            status__in=['tabled', 'removed']).order_by('-available_at')
    elif status_filter == 'passed':
        queryset = all_legislation.filter(Q(status='passed') | Q(passed=True, voting_closed=True)).order_by('-voting_ended_at')
    elif status_filter == 'failed':
        queryset = all_legislation.filter(
            Q(status='failed') |
            (Q(passed=False) & Q(voting_closed=True))
        ).exclude(status__in=['passed', 'tabled', 'removed']).order_by('-voting_ended_at')
    elif status_filter == 'tabled':
        queryset = all_legislation.filter(status='tabled').order_by('-available_at')
    else:
        # All - show closed legislation (passed + failed)
        queryset = all_legislation.filter(voting_closed=True).order_by('-voting_ended_at')

    # Count for each status tab.
    #
    # v3.17.2: was six separate COUNT round trips over the same table, one per
    # tab. They are six different predicates, but conditional aggregation
    # evaluates all of them in a single pass — `Count` with a `filter=` counts
    # only the rows matching that Q. The `personal` tab counts a different table
    # so it stays its own query.
    _passed_q = Q(status='passed') | Q(passed=True, voting_closed=True)
    _failed_q = (
        (Q(status='failed') | (Q(passed=False) & Q(voting_closed=True)))
        & ~Q(status__in=['passed', 'tabled', 'removed'])
    )
    _active_tab_q = _active_q & ~Q(status__in=['tabled', 'removed', 'pending'])

    # Aliases are prefixed because an aggregate may not share a name with a model
    # field — `passed` is both a tab and a BooleanField, and Django rejects the
    # collision with "Cannot compute Count('passed'): 'passed' is an aggregate".
    _counts = all_legislation.aggregate(
        n_all=Count('pk', filter=Q(voting_closed=True), distinct=True),
        n_pending=Count('pk', filter=_pending_q, distinct=True),
        n_active=Count('pk', filter=_active_tab_q, distinct=True),
        n_passed=Count('pk', filter=_passed_q, distinct=True),
        n_failed=Count('pk', filter=_failed_q, distinct=True),
        n_tabled=Count('pk', filter=Q(status='tabled'), distinct=True),
    )
    status_counts = {
        'all': _counts['n_all'],
        'pending': _counts['n_pending'],
        'active': _counts['n_active'],
        'passed': _counts['n_passed'],
        'failed': _counts['n_failed'],
        'tabled': _counts['n_tabled'],
        'personal': Vote.objects.filter(user=request.user)
                        .values('legislation').distinct().count(),
    }

    # Annotate vote counts onto the queryset so the loop makes zero per-leg
    # count queries for yes/no/abstain/total. Historical overrides are applied
    # after, so the annotated values serve as fallbacks only.
    queryset = queryset.annotate(
        yes_count=Count('vote', filter=Q(vote__vote_choice='yes')),
        no_count=Count('vote', filter=Q(vote__vote_choice='no')),
        abstain_count=Count('vote', filter=Q(vote__vote_choice='abstain')),
        total_count=Count('vote'),
    )

    # v3.17.3: the loop below used to `continue` past vote-less legislation that
    # isn't passed and has no explicit status — dropping rows AFTER they had
    # been counted and paginated. That was survivable while pagination happened
    # last; now that we paginate the queryset it would give ragged pages (19
    # rows, or 3) and a `total_count` that overstates what is actually shown.
    #
    # So the same predicate is expressed once, here, in SQL. Written as the
    # KEEP condition rather than a negated skip: a row survives if it has real
    # yes/no votes, or it passed, or its status is one the page always shows.
    # Historical overrides win over the annotated counts, exactly as the loop
    # does. The skip only ever applied when the user is NOT on a specific
    # status tab (on those tabs every matching row is shown by definition), so
    # the filter is applied under the same condition.
    if status_filter not in _ALWAYS_SHOWN_STATUSES:
        queryset = queryset.annotate(
            _effective_non_abstain=(
                Coalesce('historical_yes_votes', 'yes_count')
                + Coalesce('historical_no_votes', 'no_count')
            )
        ).filter(
            Q(_effective_non_abstain__gt=0)
            | Q(passed=True)
            | Q(status__in=_ALWAYS_SHOWN_STATUSES)
        )

    # v3.17.1 perf — dev mode surfaced two 6× N+1 groups here, both fired
    # lazily during template rendering (the stack pointed at the render() call,
    # which is what lazy evaluation looks like).
    #
    #  * `posted_by`: user_id is ParliamentUser's PRIMARY KEY, so every row
    #    re-fetched the same author by pk. select_related joins it once.
    #  * `co_authors`: the template iterates it per row. prefetch_related makes
    #    that one query for the whole page.
    # v3.17.2: narrow BOTH relations. select_related and prefetch_related each
    # fetch every column by default, so the earlier fix removed the N+1 while
    # still dragging ~43 ParliamentUser columns per author and per co-author.
    # posted_by is deferred (see MEMBER_PROFILE_FIELDS on why defer beats only
    # for a related queryset); co_authors gets an explicit Prefetch queryset,
    # which is the only way to narrow a prefetch.
    queryset = (
        queryset
        .select_related('posted_by')
        .defer(*member_defer('posted_by'))
        .prefetch_related(Prefetch(
            'co_authors',
            queryset=ParliamentUser.objects.only(*MEMBER_DISPLAY_FIELDS),
        ))
    )

    # v3.17.3: PAGINATE FIRST. Until now this view built its per-row dicts for
    # every piece of legislation the filter matched — computing percentages,
    # formatting attendance and calling reverse() four times each — and only
    # then handed the finished list to Paginator, which threw away all but 20.
    # Every cost below is therefore proportional to the size of the archive
    # rather than the size of the page, which is the wrong axis: the page shows
    # 20 items in a chapter's first year and 20 in its tenth.
    #
    # Paginating the *queryset* means the joins and prefetch above apply to the
    # 20 rows on screen, `windows` spans ~20 meetings instead of the whole
    # archive, and the attendance scan below is bounded. `status_counts` is
    # deliberately computed further up, on the unpaginated queryset, because the
    # tab badges must count everything.
    #
    # NOTE the two page objects: `page_obj` pages Legislation instances and
    # exists only to drive includes/pagination.html; the template iterates
    # `passed_legislation`, which is the list of display dicts built below.
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    legislation_list = list(page_obj.object_list)

    # Attendance was one query per legislation — `_present_members_in_window`
    # inside the loop. Every window is `vote_end - 6h .. vote_end`, so fetch the
    # union of all windows once and bucket it in Python.
    #
    # v3.17.3: the union here is a bounding box (min start .. max end), not a
    # union of the individual windows, so before pagination it spanned the
    # chapter's entire history and pulled every present/late row in it — then
    # re-scanned that whole list once per bill (bills × rows). Both are now
    # bounded by the page: ~20 windows, so the box is ~20 meetings wide, and the
    # single bucketing pass below replaces the per-bill re-scan.
    windows = {}
    for leg in legislation_list:
        end = leg.voting_ended_at or leg.voting_starts_at or leg.available_at
        if end:
            windows[leg.pk] = (end - timedelta(hours=6), end)

    attendance_rows = []
    if windows:
        attendance_rows = list(
            Attendance.objects
            .filter(
                status__in=('present', 'late'),
                created_at__range=(
                    min(start for start, _ in windows.values()),
                    max(end for _, end in windows.values()),
                ),
            )
            .order_by('user_id', '-created_at')
            .select_related('user')
            # v3.17.1: this page renders one thing about each attendee — their
            # name. `select_related('user')` without `.only()` was joining all
            # ~43 ParliamentUser columns per attendance row: the bio, five JSON
            # fields, six social handles, house assignment. Narrow it to the
            # columns actually rendered. See MEMBER_DISPLAY_FIELDS.
            .only(*ATTENDANCE_DISPLAY_FIELDS)
        )

    # One pass over the fetched rows fills every window's bucket, instead of
    # re-walking the whole list once per legislation. Rows arrive ordered by
    # (user_id, -created_at) and appending preserves that, which is the
    # ordering contract `_dedupe_latest_per_user` relies on.
    attendance_by_leg = {pk: [] for pk in windows}
    for row in attendance_rows:
        created = row.created_at
        for pk, (start, end) in windows.items():
            if start <= created <= end:
                attendance_by_leg[pk].append(row)

    # v3.17.3: plurality bills needed a per-choice tally, and the loop below was
    # doing one GROUP BY per bill for it — the last per-row query on this page.
    # One query covers every plurality bill on the page; non-plurality bills
    # don't need it, so the query is skipped entirely when there are none.
    plurality_pks = [
        leg.pk for leg in legislation_list
        if leg.vote_mode == 'plurality' and leg.plurality_options
    ]
    plurality_tally = {}
    if plurality_pks:
        for row in (Vote.objects
                    .filter(legislation_id__in=plurality_pks)
                    .values('legislation_id', 'vote_choice')
                    .annotate(count=Count('id'))):
            plurality_tally.setdefault(row['legislation_id'], {})[row['vote_choice']] = row['count']

    passed = []

    for leg in legislation_list:
        # Prefer historical overrides; fall back to annotated DB counts
        yes = leg.historical_yes_votes if leg.historical_yes_votes is not None else leg.yes_count
        no = leg.historical_no_votes if leg.historical_no_votes is not None else leg.no_count
        abstain = leg.historical_abstain_votes if leg.historical_abstain_votes is not None else leg.abstain_count

        total_non_abstain = yes + no
        # For plurality, total_count is the real votes-cast figure (yes/no don't apply)
        total_cast = leg.total_count if leg.vote_mode == 'plurality' else total_non_abstain + abstain

        # Skip legislation with no votes UNLESS:
        # - It's marked as passed
        # - It's tabled, pending, or active (these should show regardless of votes)
        # - We're filtering by a specific status (user wants to see all items in that status)
        #
        # v3.17.3: this is now enforced in SQL before pagination (see the KEEP
        # filter above), so it should never fire. Kept as the invariant check —
        # if the two ever disagree, dropping the row here is the safe direction,
        # and it keeps the rule readable next to the numbers it depends on.
        if total_non_abstain == 0 and not leg.passed:
            # Always show if filtering by specific status
            if status_filter in _ALWAYS_SHOWN_STATUSES:
                pass  # Don't skip
            # Always show items whose status is explicitly set
            elif leg.status in _ALWAYS_SHOWN_STATUSES:
                pass  # Don't skip
            else:
                continue

        vote_passed = False
        yes_pct = 0

        # If there are no votes, use the stored passed status
        if total_non_abstain == 0:
            vote_passed = leg.passed
            yes_pct = 0
        elif leg.vote_mode == 'piecewise':
            vote_passed = yes >= leg.required_yes_votes
        elif leg.vote_mode == 'plurality':
            # For plurality, use the stored passed status
            vote_passed = leg.passed
        else:  # percentage mode
            yes_pct = (yes / total_non_abstain) * 100
            required_pct = int(leg.required_percentage)
            vote_passed = yes_pct >= required_pct

        # Calculate vote breakdown based on mode
        if leg.vote_mode == 'plurality' and leg.plurality_options:
            # v3.17.3: read from the page-wide tally built above (was one
            # GROUP BY per plurality bill).
            raw_map = plurality_tally.get(leg.pk, {})
            vote_breakdown = {option: raw_map.get(option, 0) for option in leg.plurality_options}
            winner = max(vote_breakdown, key=vote_breakdown.get) if vote_breakdown else None
        else:
            vote_breakdown = {'yes': yes, 'no': no, 'abstain': abstain}
            winner = None


        # Determine time range for attendance window (only if there were votes)
        # Use total_cast so plurality votes (which have no yes/no) still get attendance
        present_members = []
        if total_cast > 0 and leg.pk in windows:
            # v3.17.1: read from the single batched fetch above rather than one
            # query per legislation. v3.17.3: read from this legislation's
            # pre-built bucket rather than re-filtering every fetched row.
            # Same semantics — latest present/late row per user inside this
            # legislation's own 6-hour window.
            present_members = _dedupe_latest_per_user(attendance_by_leg[leg.pk])

        # Calculate percentages for display
        if leg.vote_mode != 'plurality':
            yes_pct_display = round(yes_pct, 2) if yes_pct > 0 else 0
            no_pct_display = round((no / total_non_abstain) * 100, 2) if total_non_abstain > 0 else 0
        else:
            yes_pct_display = 0
            no_pct_display = 0

        passed.append({
            'legislation': leg,
            'yes': yes,
            'no': no,
            'abstain': abstain,
            'yes_pct': yes_pct_display,
            'no_pct': no_pct_display,
            'required_pct': int(leg.required_percentage) if leg.vote_mode == 'percentage' else None,
            'required_yes_votes': leg.required_yes_votes if leg.vote_mode == 'piecewise' else None,
            'vote_mode': leg.vote_mode,
            'vote_passed': vote_passed,
            'present_members': present_members,
            'document_url': leg.document.url if leg.document else None,
            'document_viewer_url': reverse('view_document', args=[leg.id]) if leg.document else None,
            'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.id}),
            'vote_breakdown': vote_breakdown,
            'winner': winner,
        })

        # v3.17.3: was logger.info with an f-string listing every attendee by
        # name. Two problems: it wrote member attendance rosters into the
        # application log on an ordinary GET — member data outside the app's own
        # access controls, in a file that gets rotated, backed up and read
        # during debugging — and the f-string was built even when INFO was
        # filtered out. %-style args are only interpolated if the record is
        # actually emitted, and the count is the part that was ever useful.
        if present_members:
            logger.debug('%s present members: %d', leg.title, len(present_members))

    # Pagination happened before the loop above (see the note there). `passed`
    # is already just this page's rows; `page_obj` drives the page controls.
    return render(request, 'passed_legislation.html', {
        'passed_legislation': passed,
        'page_obj': page_obj,
        'total_count': paginator.count,
        'status_filter': status_filter,
        'status_counts': status_counts,
        'personal_ballots': personal_ballots,
    })


class PassedLegislationDetailView(LoginRequiredMixin, DetailView):
    # v3.13.3: LoginRequiredMixin added — this view had NO auth check (the
    # function views here use @login_required, but this CBV had no mixin and
    # there is no global login middleware), so vote results, individual voter
    # names/choices, and the present-members list were publicly accessible.
    model = Legislation
    template_name = 'src/legislation_detail.html'
    context_object_name = 'legislation'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        legislation = self.object
        # v3.17.3: `select_related('user')` here fed one thing — the voter's
        # name in the individual-votes table. Narrow it to the display columns
        # rather than joining all ~43 ParliamentUser columns per ballot.
        # See MEMBER_DISPLAY_FIELDS in src/models/users.py.
        votes = (
            Vote.objects.filter(legislation=legislation)
            .select_related('user')
            .only('id', 'user_id', 'legislation_id', 'vote_choice', 'cast_at',
                  *(f'user__{f}' for f in MEMBER_DISPLAY_FIELDS))
        )

        # v3.17.3: one GROUP BY replaces what was up to `len(options) + 4`
        # separate COUNT round trips — one per plurality option, plus yes/no/
        # abstain/total. This is the third site of the pattern v3.17.1 and
        # v3.17.2 each fixed once; the shape is identical to the tally in
        # view_legislation_history.
        # Built from a plain queryset rather than off `votes` above: .values()
        # discards select_related/only anyway, and keeping them separate makes
        # it obvious that this is an aggregate, not a row fetch.
        tally = {
            row['vote_choice']: row['n']
            for row in Vote.objects.filter(legislation=legislation)
                                   .values('vote_choice')
                                   .annotate(n=Count('id'))
        }
        cast_total = sum(tally.values())

        if legislation.vote_mode == 'plurality':
            options = legislation.plurality_options or []
            vote_counts = {opt: tally.get(opt, 0) for opt in options}
            winner = max(vote_counts, key=vote_counts.get) if vote_counts else None
            context['vote_result'] = {
                'mode': 'plurality',
                'options': vote_counts,
                'winner': winner,
                'total': cast_total,
            }

        elif legislation.vote_mode == 'piecewise':
            yes = legislation.historical_yes_votes if legislation.historical_yes_votes is not None else tally.get('yes', 0)
            no  = legislation.historical_no_votes  if legislation.historical_no_votes  is not None else tally.get('no', 0)
            abstain = legislation.historical_abstain_votes if legislation.historical_abstain_votes is not None else tally.get('abstain', 0)
            required = legislation.required_number or 0
            context['vote_result'] = {
                'mode': 'piecewise',
                'yes': yes,
                'no': no,
                'abstain': abstain,
                'required_yes': required,
                'passed': yes >= required,
                'total': yes + no + abstain,
            }

        else:  # percentage
            yes = legislation.historical_yes_votes if legislation.historical_yes_votes is not None else tally.get('yes', 0)
            no  = legislation.historical_no_votes  if legislation.historical_no_votes  is not None else tally.get('no', 0)
            abstain = legislation.historical_abstain_votes if legislation.historical_abstain_votes is not None else tally.get('abstain', 0)
            countable = yes + no
            yes_pct = (yes / countable * 100) if countable > 0 else 0
            required_pct = int(legislation.required_percentage)
            context['vote_result'] = {
                'mode': 'percentage',
                'yes': yes,
                'no': no,
                'abstain': abstain,
                'yes_percentage': "{:.0f}%".format(yes_pct),
                'yes_pct_num': round(yes_pct, 1),
                'required_percentage': required_pct,
                'passed': yes_pct >= required_pct,
                'total': yes + no + abstain,
            }

        # Individual votes (only if not anonymous and votes exist in DB).
        # v3.17.3: `votes.exists()` then `votes.count()` were two more round
        # trips asking what the tally above already answered.
        if not legislation.anonymous_vote and cast_total > 0:
            context['individual_votes'] = list(votes.order_by('user__name'))

        # Present members: look back 6 hours from when voting ended
        total_cast = cast_total
        if total_cast > 0:
            vote_end = legislation.voting_ended_at or legislation.voting_starts_at or legislation.available_at
            vote_start = vote_end - timedelta(hours=6)
            context['present_members'] = _present_members_in_window(vote_start, vote_end)

        return context


@login_required
@require_http_methods(["POST"])
@log_function_call
def add_legislation(request):
    """
    Add new legislation to the tracker.
    Officers and admins can add legislation with title, status, description,
    optional document, and optional vote results.
    """
    # Check permissions
    if not request.user.is_admin and request.user.member_type != 'Officer':
        messages.error(request, 'You do not have permission to add legislation.')
        return redirect('passed_legislation')

    title = request.POST.get('title', '').strip()
    status = request.POST.get('status', 'pending')
    description = request.POST.get('description', '').strip()
    document = request.FILES.get('document')
    include_votes = request.POST.get('include_votes') == 'on'

    # Validation
    if not title:
        messages.error(request, 'Title is required.')
        return redirect('passed_legislation')

    if not document and len(description) < 20:
        messages.error(request, 'Please provide either a document or a detailed description (at least 20 characters).')
        return redirect('passed_legislation')

    # Validate status
    valid_statuses = ['pending', 'active', 'passed', 'failed', 'tabled']
    if status not in valid_statuses:
        status = 'pending'

    # Determine if voting is closed based on status
    # Tabled legislation also has voting closed (it's on hold, not being voted on)
    voting_closed = status in ['passed', 'failed', 'tabled']
    passed = status == 'passed'

    # Create the legislation
    now = timezone.now()
    legislation = Legislation.objects.create(
        title=title,
        description=description,
        document=document,
        status=status,
        posted_by=request.user,
        available_at=now,
        voting_starts_at=now,
        voting_closed=voting_closed,
        passed=passed,
        required_percentage=request.POST.get('required_percentage', '51'),
    )

    # If voting is closed (passed, failed, or tabled), set voting_ended_at
    if voting_closed:
        legislation.voting_ended_at = now
        legislation.save(update_fields=['voting_ended_at'])

    # For pending status, set voting_starts_at to future (so it doesn't appear as active)
    if status == 'pending':
        legislation.voting_starts_at = None
        legislation.save(update_fields=['voting_starts_at'])

    # Handle vote results if included
    if include_votes and voting_closed:
        try:
            yes_votes = int(request.POST.get('yes_votes', 0))
            no_votes = int(request.POST.get('no_votes', 0))
            abstain_votes = int(request.POST.get('abstain_votes', 0))

            # Store historical vote counts on the legislation
            legislation.historical_yes_votes = yes_votes
            legislation.historical_no_votes = no_votes
            legislation.historical_abstain_votes = abstain_votes
            legislation.save(update_fields=['historical_yes_votes', 'historical_no_votes', 'historical_abstain_votes'])
        except (ValueError, TypeError):
            pass  # Ignore invalid vote counts

    logger.info(f"{request.user.username} added legislation: {title} with status {status}")
    messages.success(request, f'Legislation "{title}" has been added.')
    return redirect('passed_legislation')


@login_required
@require_http_methods(["POST"])
@log_function_call
def update_legislation_note(request, legislation_id):
    """
    Add or update an admin note on a piece of legislation.
    Only admins and officers can edit notes. The vote result is not affected.
    """
    from django.http import JsonResponse

    if not request.user.is_admin and request.user.member_type != 'Officer':
        return JsonResponse({'ok': False, 'error': 'Permission denied.'}, status=403)

    legislation = get_object_or_404(Legislation, id=legislation_id)
    note = request.POST.get('note', '').strip()
    legislation.admin_note = note
    legislation.save(update_fields=['admin_note'])

    logger.info(f"{request.user.username} updated note on legislation '{legislation.title}' (ID: {legislation_id})")
    return JsonResponse({'ok': True, 'note': note})