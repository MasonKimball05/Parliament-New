from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Q
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


# v3.17.1 — see MEMBER_DISPLAY_FIELDS in src/models/users.py. ParliamentUser is a
# wide table carrying the whole member profile; a page that only prints names has
# no business selecting the bio and five JSON columns for every joined row.
ATTENDANCE_DISPLAY_FIELDS = (
    'id', 'user_id', 'created_at', 'status',
) + tuple(f'user__{name}' for name in MEMBER_DISPLAY_FIELDS)


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
        .select_related('user')
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
    legislation_list = list(
        queryset
        .select_related('posted_by')
        .defer(*(f'posted_by__{f}' for f in MEMBER_PROFILE_FIELDS))
        .prefetch_related(Prefetch(
            'co_authors',
            queryset=ParliamentUser.objects.only(*MEMBER_DISPLAY_FIELDS),
        ))
    )

    # Attendance was one query per legislation — `_present_members_in_window`
    # inside the loop. Every window is `vote_end - 6h .. vote_end`, so fetch the
    # union of all windows once and slice it in Python. Row counts here are tiny
    # (≤ chapter size per meeting), which is the same reasoning that made the
    # v3.13.3 Python dedupe acceptable.
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
        if total_non_abstain == 0 and not leg.passed:
            # Always show if filtering by specific status
            if status_filter in ['tabled', 'pending', 'active', 'passed', 'failed']:
                pass  # Don't skip
            # Always show items whose status is explicitly set
            elif leg.status in ['tabled', 'pending', 'active', 'passed', 'failed']:
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
            # Single query: group all votes for this leg by choice
            raw_map = {
                row['vote_choice']: row['count']
                for row in Vote.objects.filter(legislation=leg)
                    .values('vote_choice')
                    .annotate(count=Count('id'))
            }
            vote_breakdown = {option: raw_map.get(option, 0) for option in leg.plurality_options}
            winner = max(vote_breakdown, key=vote_breakdown.get) if vote_breakdown else None
        else:
            vote_breakdown = {'yes': yes, 'no': no, 'abstain': abstain}
            winner = None


        # Determine time range for attendance window (only if there were votes)
        # Use total_cast so plurality votes (which have no yes/no) still get attendance
        present_members = []
        if total_cast > 0 and leg.pk in windows:
            # v3.17.1: sliced from the single batched fetch above rather than
            # one query per legislation. Same semantics — latest present/late
            # row per user inside this legislation's own 6-hour window.
            vote_start, vote_end = windows[leg.pk]
            present_members = _dedupe_latest_per_user(
                row for row in attendance_rows
                if vote_start <= row.created_at <= vote_end
            )

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

        if present_members:
            logger.info(f"{leg.title} present members: {[a.user.name for a in present_members]}")

    # Pagination - 20 items per page
    paginator = Paginator(passed, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'passed_legislation.html', {
        'passed_legislation': page_obj,
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
        votes = Vote.objects.filter(legislation=legislation).select_related('user')

        if legislation.vote_mode == 'plurality':
            options = legislation.plurality_options or []
            vote_counts = {opt: votes.filter(vote_choice=opt).count() for opt in options}
            winner = max(vote_counts, key=vote_counts.get) if vote_counts else None
            context['vote_result'] = {
                'mode': 'plurality',
                'options': vote_counts,
                'winner': winner,
                'total': votes.count(),
            }

        elif legislation.vote_mode == 'piecewise':
            yes = legislation.historical_yes_votes if legislation.historical_yes_votes is not None else votes.filter(vote_choice='yes').count()
            no  = legislation.historical_no_votes  if legislation.historical_no_votes  is not None else votes.filter(vote_choice='no').count()
            abstain = legislation.historical_abstain_votes if legislation.historical_abstain_votes is not None else votes.filter(vote_choice='abstain').count()
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
            yes = legislation.historical_yes_votes if legislation.historical_yes_votes is not None else votes.filter(vote_choice='yes').count()
            no  = legislation.historical_no_votes  if legislation.historical_no_votes  is not None else votes.filter(vote_choice='no').count()
            abstain = legislation.historical_abstain_votes if legislation.historical_abstain_votes is not None else votes.filter(vote_choice='abstain').count()
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

        # Individual votes (only if not anonymous and votes exist in DB)
        if not legislation.anonymous_vote and votes.exists():
            context['individual_votes'] = list(votes.order_by('user__name'))

        # Present members: look back 6 hours from when voting ended
        total_cast = votes.count()
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