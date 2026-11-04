from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.committee_management import (
    CommitteeActionError, can_manage_chapter_vote, committee_result_summary, create_chapter_vote, push_to_chapter,
)
from src.models import Committee, CommitteeLegislation
from src.feature_flag_decorators import require_feature_flag
import logging
from django.utils import timezone

logger = logging.getLogger('function_calls')


@login_required
@require_feature_flag('committee_voting')
def committee_push_to_chapter(request, code):
    """Publish committee vote results to chapter documents page (no chapter vote created)"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can publish results to chapter.')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        # Just mark as published - don't create a chapter vote
        try:
            push_to_chapter(committee_leg, request.user)
        except CommitteeActionError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(request, f"'{committee_leg.title}' results published to chapter documents.")

        # Redirect back to referring page if specified (validate to prevent open redirect)
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect(f'/committee/{code}/vote/')

    # Get passed committee legislation
    passed_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        status='passed',
        voting_closed=True
    ).order_by('-created_at')

    return render(request, 'committee/push_to_chapter.html', {
        'committee': committee,
        'passed_legislation': passed_legislation,
    })


@login_required
@require_feature_flag('committee_voting', 'legislation_voting')
def create_chapter_vote_from_committee(request, code, legislation_id):
    """Create a new chapter-wide vote based on a committee vote - with configuration options"""
    committee = get_object_or_404(Committee, code=code)
    committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

    # Check permissions
    if not can_manage_chapter_vote(request.user, committee):
        messages.error(request, 'Only committee chairs or admins can create chapter votes.')
        return redirect('committee_home', code=code)

    # Check if chapter vote already exists
    if committee_leg.chapter_legislation:
        messages.error(request, 'A chapter vote already exists for this item.')
        return redirect(f'/committee/{code}/vote/')

    if request.method == 'POST':
        # Get form data
        title = request.POST.get('title', f"[{committee.code}] {committee_leg.title}")
        description = request.POST.get('description', committee_leg.description or '')
        vote_mode = request.POST.get('vote_mode', 'percentage')
        required_percentage = request.POST.get('required_percentage', '51')
        required_number = request.POST.get('required_number')

        # Parse available_at
        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import make_aware
        raw_available_at = request.POST.get('available_at')
        if raw_available_at:
            parsed = parse_datetime(raw_available_at)
            available_at = make_aware(parsed) if parsed else timezone.now()
        else:
            available_at = timezone.now()

        # Validate, build the description and create the linked chapter vote
        # (src.committee_management, shared with the app).
        try:
            create_chapter_vote(
                committee, committee_leg, request.user,
                title=title,
                description=description,
                vote_mode=vote_mode,
                required_percentage=required_percentage,
                required_number=required_number,
                plurality_options=([request.POST.get(f'plurality_option_{i}') for i in range(1, 6)]
                                   if vote_mode == 'plurality' else []),
                anonymous=request.POST.get('anonymous_vote') == 'on',
                remove_abstain=request.POST.get('remove_abstain') == 'on',
                show_committee_result=request.POST.get('show_committee_result') == 'on',
                committee_vote_summary=request.POST.get('committee_vote_summary', ''),
                available_at=available_at,
            )
        except CommitteeActionError as exc:
            messages.error(request, exc.message)
            return redirect(request.path)

        messages.success(request, f"Chapter vote created! Members can now vote on the Vote page.")

        return redirect(f'/committee/{code}/vote/')

    # GET request - show the configuration form, with the recorded committee result
    return render(request, 'committee/create_chapter_vote.html', {
        'committee': committee,
        'committee_leg': committee_leg,
        **committee_result_summary(committee_leg),
    })