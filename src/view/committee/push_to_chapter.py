from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeLegislation, Legislation
import logging
from django.utils import timezone

@login_required
def committee_push_to_chapter(request, code):
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can push items to chapter votes.')
        return redirect('committee_detail', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        # Check if already pushed
        if committee_leg.pushed_to_chapter:
            messages.error(request, 'This item has already been pushed to chapter.')
            return redirect('push_to_chapter', code=code)

        # Create chapter legislation
        chapter_leg = Legislation.objects.create(
            title=f"[{committee.code}] {committee_leg.title}",
            description=committee_leg.description,
            document=committee_leg.document,
            posted_by=request.user,
            available_at=timezone.now(),
            anonymous_vote=committee_leg.anonymous_vote,
            allow_abstain=committee_leg.allow_abstain,
            required_percentage=committee_leg.required_percentage,
            vote_mode=committee_leg.vote_mode,
            plurality_options=committee_leg.plurality_options,
        )

        # Mark as pushed
        committee_leg.pushed_to_chapter = True
        committee_leg.chapter_legislation = chapter_leg
        committee_leg.save()

        logger = logging.getLogger('function_calls')
        logger.info(f"{request.user.username} pushed '{committee_leg.title}' from {committee.code} to chapter vote")

        messages.success(request, f"'{committee_leg.title}' has been pushed to chapter vote.")
        return redirect('push_to_chapter', code=code)

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