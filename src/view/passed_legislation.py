from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import DetailView
from django.views.decorators.http import require_http_methods
from django.urls import reverse
import logging
from ..decorators import log_function_call
from ..models import Legislation, Vote, Attendance
from src.feature_flag_decorators import require_page_enabled

logger = logging.getLogger(__name__)
from datetime import timedelta

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
    if status_filter == 'pending':
        # Pending: voting hasn't started yet
        queryset = all_legislation.filter(
            Q(status='pending') |
            (Q(voting_starts_at__gt=now) & Q(voting_closed=False))
        ).order_by('-available_at')
    elif status_filter == 'active':
        # Active: voting is open
        queryset = all_legislation.filter(
            Q(status='active') |
            (Q(voting_closed=False) & Q(voting_starts_at__lte=now))
        ).exclude(status__in=['tabled', 'removed']).order_by('-available_at')
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

    # Count for each status tab
    status_counts = {
        'all': all_legislation.filter(voting_closed=True).count(),
        'pending': all_legislation.filter(
            Q(status='pending') | (Q(voting_starts_at__gt=now) & Q(voting_closed=False))
        ).count(),
        'active': all_legislation.filter(
            Q(status='active') | (Q(voting_closed=False) & Q(voting_starts_at__lte=now))
        ).exclude(status__in=['tabled', 'removed', 'pending']).count(),
        'passed': all_legislation.filter(Q(status='passed') | Q(passed=True, voting_closed=True)).count(),
        'failed': all_legislation.filter(
            Q(status='failed') | (Q(passed=False) & Q(voting_closed=True))
        ).exclude(status__in=['passed', 'tabled', 'removed']).count(),
        'tabled': all_legislation.filter(status='tabled').count(),
    }

    passed = []

    for leg in queryset:
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        abstain = votes.filter(vote_choice='abstain').count()

        # Use historical vote counts if available (for manually entered legislation)
        if leg.historical_yes_votes is not None:
            yes = leg.historical_yes_votes
        if leg.historical_no_votes is not None:
            no = leg.historical_no_votes
        if leg.historical_abstain_votes is not None:
            abstain = leg.historical_abstain_votes

        total_non_abstain = yes + no
        # For plurality votes, determine if any votes were cast via total vote count
        total_cast = votes.count() if leg.vote_mode == 'plurality' else total_non_abstain + abstain

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
            # For plurality, get counts for each option
            vote_breakdown = {}
            for option in leg.plurality_options:
                vote_breakdown[option] = votes.filter(vote_choice=option).count()
            winner = max(vote_breakdown, key=vote_breakdown.get) if vote_breakdown else None
        else:
            # For yes/no votes
            vote_breakdown = {
                'yes': yes,
                'no': no,
                'abstain': abstain
            }
            winner = None


        # Determine time range for attendance window (only if there were votes)
        # Use total_cast so plurality votes (which have no yes/no) still get attendance
        present_members = []
        if total_cast > 0:
            # Anchor the window on when voting started (or ended) and look back up to 6 hours
            # to capture attendance marked at the beginning of a long meeting
            vote_end = leg.voting_ended_at or leg.voting_starts_at or leg.available_at
            vote_start = vote_end - timedelta(hours=6)

            # Only get the latest attendance record per user in the window
            present_members = Attendance.objects.filter(
                present=True,
                created_at__range=(vote_start, vote_end)
            ).order_by('user_id', '-created_at').distinct('user_id').select_related('user')

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
    })


class PassedLegislationDetailView(DetailView):
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
            context['present_members'] = (
                Attendance.objects.filter(
                    present=True,
                    created_at__range=(vote_start, vote_end)
                ).order_by('user_id', '-created_at').distinct('user_id').select_related('user')
            )

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
        legislation.save()

    # For pending status, set voting_starts_at to future (so it doesn't appear as active)
    if status == 'pending':
        legislation.voting_starts_at = None
        legislation.save()

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
            legislation.save()
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