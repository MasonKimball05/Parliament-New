from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from ..models import *
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_page_enabled
import logging

@login_required
@require_page_enabled('vote')
def vote_view(request):
    user = request.user

    # Handle legislation upload
    if user.member_type in ['Chair', 'Officer'] and request.method == 'POST' and 'title' in request.POST:
        title = request.POST.get('title')
        description = request.POST.get('description')
        document = request.FILES.get('document')

        # Validate uploaded file for security
        if document:
            try:
                validate_uploaded_file(document)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return redirect('home')

        anonymous = request.POST.get('anonymous') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        required_percentage = int(request.POST.get('required_percentage', 51))

        raw_available_at = request.POST.get('available_at')
        parsed_available_at = parse_datetime(raw_available_at)
        available_at = make_aware(parsed_available_at) if parsed_available_at else None

        # Parse voting_starts_at (optional - defaults to available_at if not set)
        raw_voting_starts_at = request.POST.get('voting_starts_at')
        voting_starts_at = None
        if raw_voting_starts_at:
            parsed_voting_starts_at = parse_datetime(raw_voting_starts_at)
            voting_starts_at = make_aware(parsed_voting_starts_at) if parsed_voting_starts_at else None

        # Parse voting_ends_at (optional)
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
                return redirect('vote')

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
                return redirect('vote')
            required_number = int(required_number)

        if title and description and available_at and (document or vote_mode == 'plurality'):
            Legislation.objects.create(
                title=title,
                description=description,
                document=document if vote_mode != 'plurality' else None,
                posted_by=user,
                available_at=available_at,
                voting_starts_at=voting_starts_at,
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
            logger.info(f"{user.username} uploaded legislation titled '{title}' (mode: {vote_mode}, required %: {required_percentage})")

            messages.success(request, "Legislation uploaded successfully.")
            return redirect('vote')

    # Determine if user is present and allowed to vote
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        present=True
    ).order_by('-created_at').first()
    # Check both attendance AND if user type can vote (excludes pledges)
    can_vote = bool(attendance) and user.can_vote

    # Handle voting
    if request.method == 'POST' and ('vote_choice' in request.POST or 'vote_choices' in request.POST) and can_vote:
        password = request.POST.get('password')
        auth_user = authenticate(request, username=user.username, password=password)

        if auth_user:
            legislation_id = request.POST.get('legislation_id')
            legislation = get_object_or_404(Legislation, id=legislation_id)

            if Vote.objects.filter(user=user, legislation=legislation).exists():
                messages.error(request, "You have already voted on this legislation.")
                return redirect('vote')
            if legislation.voting_closed:
                messages.error(request, "Voting on this legislation has ended.")
                return redirect('vote')
            if not legislation.voting_has_started():
                messages.error(request, "Voting has not started yet on this legislation.")
                return redirect('vote')

            logger = logging.getLogger('function_calls')

            # Handle multi-select plurality voting
            if legislation.vote_mode == 'plurality' and legislation.plurality_votes_allowed > 1:
                vote_choices = request.POST.getlist('vote_choices')

                # Validate number of selections
                if len(vote_choices) < 1:
                    messages.error(request, "Please select at least one option.")
                    return redirect('vote')
                if len(vote_choices) > legislation.plurality_votes_allowed:
                    messages.error(request, f"You can only select up to {legislation.plurality_votes_allowed} options.")
                    return redirect('vote')

                # Validate each choice
                for choice in vote_choices:
                    if choice not in legislation.plurality_options:
                        messages.error(request, "Invalid vote option.")
                        return redirect('vote')

                # Create a vote record for each selection
                for choice in vote_choices:
                    Vote.objects.create(user=user, legislation=legislation, vote_choice=choice)

                logger.info(f"{user.username} voted for {vote_choices} on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
                messages.success(request, f"Your {len(vote_choices)} vote(s) have been submitted.")
            else:
                # Single-select voting (percentage, piecewise, or plurality with 1 vote)
                vote_choice = request.POST.get('vote_choice')
                if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                    messages.error(request, "Invalid vote option.")
                    return redirect('vote')

                Vote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)
                logger.info(f"{user.username} voted '{vote_choice}' on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
                messages.success(request, "Your vote has been submitted.")

            return redirect('vote')
        else:
            messages.error(request, "Incorrect password.")
            return redirect('vote')

    # Gather available legislation
    # Show legislation that is available OR pending legislation created by the current user
    # Exclude tabled, passed, failed, and removed legislation
    from django.db.models import Q
    available_legislation = Legislation.objects.filter(
        Q(available_at__lte=timezone.now()) | Q(posted_by=user),
        voting_closed=False
    ).exclude(
        status__in=['pending', 'tabled', 'passed', 'failed', 'removed']
    ).order_by('-available_at')

    # Build vote data for uploader
    vote_data = {}
    for leg in available_legislation:
        if leg.posted_by == user:
            votes = Vote.objects.filter(legislation=leg)
            if leg.vote_mode == 'plurality':
                tally = {opt: votes.filter(vote_choice=opt).count() for opt in leg.plurality_options}
                tally['total'] = votes.count()
                vote_data[leg.id] = tally
            else:
                vote_data[leg.id] = {
                    'yes': votes.filter(vote_choice='yes').count(),
                    'no': votes.filter(vote_choice='no').count(),
                    'abstain': votes.filter(vote_choice='abstain').count(),
                    'total': votes.count()
                }

    return render(request, 'vote.html', {
        'profile': user,
        'can_vote': can_vote,
        'legislation': available_legislation,
        'vote_data': vote_data,
        'default_vote_mode': 'percentage',
    })
