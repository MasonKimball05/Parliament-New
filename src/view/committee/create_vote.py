from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeLegislation
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from django.views.decorators.http import require_http_methods
import logging

@require_http_methods(["GET", "POST"])
@login_required
def committee_create_vote(request, code):
    committee = get_object_or_404(Committee, code=code)

    # Check permissions - only chairs can create votes
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can create votes.')
        return redirect('committee_detail', code=code)

    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        document = request.FILES.get('document')
        anonymous = request.POST.get('anonymous') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        required_percentage = request.POST.get('required_percentage', '51')

        raw_available_at = request.POST.get('available_at')
        parsed_available_at = parse_datetime(raw_available_at)
        available_at = make_aware(parsed_available_at) if parsed_available_at else None

        vote_mode = request.POST.get('vote_mode', 'percentage')
        plurality_options = []

        if vote_mode == 'plurality':
            for i in range(1, 6):
                val = request.POST.get(f'plurality_option_{i}')
                if val:
                    plurality_options.append(val.strip())

            if len(plurality_options) < 2:
                messages.error(request, "Plurality voting requires at least two options.")
                return redirect('create_committee_vote', code=code)

        if title and description and available_at and (document or vote_mode == 'plurality'):
            CommitteeLegislation.objects.create(
                committee=committee,
                title=title,
                description=description,
                document=document if vote_mode != 'plurality' else None,
                posted_by=request.user,
                available_at=available_at,
                anonymous_vote=anonymous,
                allow_abstain=allow_abstain,
                required_percentage=required_percentage,
                vote_mode=vote_mode,
                plurality_options=plurality_options if vote_mode == 'plurality' else None
            )

            logger = logging.getLogger('function_calls')
            logger.info(f"{request.user.username} created committee legislation titled '{title}' for {committee.code}")

            messages.success(request, "Committee legislation created successfully.")
            return redirect('vote', code=code)
        else:
            messages.error(request, "Please fill in all required fields.")

    return render(request, 'committee/create_vote.html', {
        'committee': committee,
    })