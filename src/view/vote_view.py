from django.contrib.auth.decorators import login_required
from ..models import *
from ..decorators import *
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.timezone import make_aware
from django.contrib import messages
from django.contrib.auth import authenticate
from django.utils.dateparse import parse_datetime
from datetime import timedelta
import logging

@require_http_methods(["GET", "POST"])
@login_required
@log_function_call
def vote_view(request):
    user = request.user

    # Handle legislation upload
    if user.member_type in ['Chair', 'Officer'] and request.method == 'POST' and 'title' in request.POST:
        title = request.POST.get('title')
        description = request.POST.get('description')
        document = request.FILES.get('document')
        anonymous = request.POST.get('anonymous') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        required_percentage = int(request.POST.get('required_percentage', 51))

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
                return redirect('vote')

        if title and description and available_at and (document or vote_mode == 'plurality'):
            Legislation.objects.create(
                title=title,
                description=description,
                document=document if vote_mode != 'plurality' else None,
                posted_by=user,
                available_at=available_at,
                anonymous_vote=anonymous,
                allow_abstain=allow_abstain,
                required_percentage=required_percentage,
                vote_mode=vote_mode,
                plurality_options=plurality_options if vote_mode == 'plurality' else None
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
    can_vote = bool(attendance)

    # Handle voting
    if request.method == 'POST' and 'vote_choice' in request.POST and can_vote:
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

            vote_choice = request.POST.get('vote_choice')
            if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                messages.error(request, "Invalid vote option.")
                return redirect('vote')

            Vote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)

            logger = logging.getLogger('function_calls')
            logger.info(f"{user.username} voted '{vote_choice}' on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")

            messages.success(request, "Your vote has been submitted.")
            return redirect('vote')
        else:
            messages.error(request, "Incorrect password.")
            return redirect('vote')

    # Gather available legislation
    available_legislation = Legislation.objects.filter(
        available_at__lte=timezone.now(),
        voting_closed=False
    )

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