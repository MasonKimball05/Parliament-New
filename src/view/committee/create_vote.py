from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeLegislation
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from django.views.decorators.http import require_http_methods
import logging
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file

@require_http_methods(["GET", "POST"])
@login_required
def committee_create_vote(request, code):
    committee = get_object_or_404(Committee, code=code)

    # Check permissions - only chairs can create votes
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can create votes.')
        return redirect('committee_home', code=code)

    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        document = request.FILES.get('document')

        # Validate uploaded file if provided
        if document:
            try:
                validate_uploaded_file(document)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'committee/create_vote.html', {'committee': committee})

        anonymous = request.POST.get('anonymous') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        required_percentage = request.POST.get('required_percentage', '51')

        raw_available_at = request.POST.get('available_at')
        parsed_available_at = parse_datetime(raw_available_at)
        available_at = make_aware(parsed_available_at) if parsed_available_at else None

        # Optional voting end time
        raw_voting_ends_at = request.POST.get('voting_ends_at')
        voting_ends_at = None
        if raw_voting_ends_at:
            parsed_voting_ends_at = parse_datetime(raw_voting_ends_at)
            voting_ends_at = make_aware(parsed_voting_ends_at) if parsed_voting_ends_at else None

        vote_mode = request.POST.get('vote_mode', 'percentage')
        plurality_options = []
        required_number = None
        plurality_votes_allowed = 1
        plurality_runoff_enabled = False
        plurality_runoff_count = 2

        if vote_mode == 'plurality':
            # Support up to 10 plurality options
            for i in range(1, 11):
                val = request.POST.get(f'plurality_option_{i}')
                if val and val.strip():
                    plurality_options.append(val.strip())

            if len(plurality_options) < 2:
                messages.error(request, "Plurality voting requires at least two options.")
                return redirect('create_committee_vote', code=code)

            # Parse multi-select settings
            votes_allowed_raw = request.POST.get('plurality_votes_allowed', '1')
            try:
                plurality_votes_allowed = max(1, min(10, int(votes_allowed_raw)))
            except (ValueError, TypeError):
                plurality_votes_allowed = 1

            # Ensure votes_allowed doesn't exceed number of options
            plurality_votes_allowed = min(plurality_votes_allowed, len(plurality_options))

            # Parse runoff settings
            plurality_runoff_enabled = request.POST.get('plurality_runoff_enabled') == 'on'
            if plurality_runoff_enabled:
                runoff_count_raw = request.POST.get('plurality_runoff_count', '2')
                try:
                    plurality_runoff_count = max(2, min(len(plurality_options), int(runoff_count_raw)))
                except (ValueError, TypeError):
                    plurality_runoff_count = 2

        elif vote_mode == 'piecewise':
            required_number = request.POST.get('required_number')
            if not required_number or int(required_number) < 1:
                messages.error(request, "Piecewise voting requires a valid number of required votes (at least 1).")
                return redirect('create_committee_vote', code=code)
            required_number = int(required_number)

        # Require title, available_at, and either description OR document
        if title and available_at and (description or document):
            CommitteeLegislation.objects.create(
                committee=committee,
                title=title,
                description=description,
                document=document if vote_mode != 'plurality' else None,
                posted_by=request.user,
                available_at=available_at,
                voting_ends_at=voting_ends_at,
                anonymous_vote=anonymous,
                allow_abstain=allow_abstain,
                required_percentage=required_percentage,
                vote_mode=vote_mode,
                plurality_options=plurality_options if vote_mode == 'plurality' else None,
                plurality_votes_allowed=plurality_votes_allowed if vote_mode == 'plurality' else 1,
                plurality_runoff_enabled=plurality_runoff_enabled if vote_mode == 'plurality' else False,
                plurality_runoff_count=plurality_runoff_count if vote_mode == 'plurality' else 2,
                required_number=required_number if vote_mode == 'piecewise' else None
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