from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.committee_management import CommitteeActionError, create_committee_vote
from src.models import Committee
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from django.views.decorators.http import require_http_methods
from django.core.exceptions import ValidationError
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_feature_flag

@require_http_methods(["GET", "POST"])
@login_required
@require_feature_flag('committee_voting')
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

        raw_available_at = request.POST.get('available_at')
        parsed_available_at = parse_datetime(raw_available_at) if raw_available_at else None
        available_at = make_aware(parsed_available_at) if parsed_available_at else None

        # Optional voting end time
        raw_voting_ends_at = request.POST.get('voting_ends_at')
        voting_ends_at = None
        if raw_voting_ends_at:
            parsed_voting_ends_at = parse_datetime(raw_voting_ends_at)
            voting_ends_at = make_aware(parsed_voting_ends_at) if parsed_voting_ends_at else None

        # Validation and the create are src/committee_management (shared with
        # the app); up to 10 plurality options.
        try:
            create_committee_vote(
                committee, request.user,
                title=title,
                description=description,
                document=document,
                available_at=available_at,
                voting_ends_at=voting_ends_at,
                anonymous=request.POST.get('anonymous') == 'on',
                remove_abstain=request.POST.get('remove_abstain') == 'on',
                required_percentage=request.POST.get('required_percentage', '51'),
                vote_mode=request.POST.get('vote_mode', 'percentage'),
                plurality_options=[request.POST.get(f'plurality_option_{i}') or '' for i in range(1, 11)],
                plurality_votes_allowed=request.POST.get('plurality_votes_allowed', '1'),
                plurality_runoff_enabled=request.POST.get('plurality_runoff_enabled') == 'on',
                plurality_runoff_count=request.POST.get('plurality_runoff_count', '2'),
                required_number=request.POST.get('required_number'),
            )
        except CommitteeActionError as exc:
            messages.error(request, exc.message)
            if exc.code != 'required_fields':
                return redirect('create_committee_vote', code=code)
        else:
            messages.success(request, "Committee legislation created successfully.")
            return redirect('vote', code=code)

    return render(request, 'committee/create_vote.html', {
        'committee': committee,
    })