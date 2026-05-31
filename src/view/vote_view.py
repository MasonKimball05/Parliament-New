from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
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
            new_legislation = Legislation.objects.create(
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
            ActivityLog.log_activity(
                action_type='legislation_created',
                user=user,
                description=f'{user.name} created legislation "{title}" ({vote_mode} vote, {"anonymous" if anonymous else "non-anonymous"})',
                request=request,
                object_type='Legislation',
                object_id=new_legislation.id,
                object_repr=title,
                metadata={
                    'vote_mode': vote_mode,
                    'anonymous': anonymous,
                    'required_percentage': required_percentage if vote_mode == 'percentage' else None,
                    'required_number': required_number if vote_mode == 'piecewise' else None,
                    'plurality_options': plurality_options if vote_mode == 'plurality' else None,
                },
            )

            messages.success(request, "Legislation uploaded successfully.")
            return redirect('vote')

    # Quick attendance marking for officers (no event required)
    if request.method == 'POST' and request.POST.get('action') == 'mark_attendance_quick':
        if user.member_type in ['Chair', 'Officer']:
            target_user_id = request.POST.get('target_user_id')
            new_status = request.POST.get('attendance_status')
            if target_user_id and new_status in ['present', 'late', 'absent']:
                now = timezone.now()
                try:
                    target = ParliamentUser.objects.get(user_id=target_user_id)
                    Attendance.objects.update_or_create(
                        user=target,
                        date=now.date(),
                        attendance_type='committee',
                        committee=None,
                        defaults={
                            'status': new_status,
                            'created_at': now,
                            'marked_by': user,
                            'marked_at': now,
                        }
                    )
                except ParliamentUser.DoesNotExist:
                    pass
        return redirect('vote')

    # Determine if user is present/late and allowed to vote
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        status__in=['present', 'late']
    ).order_by('-created_at').first()
    # Check both attendance AND if user type can vote (excludes pledges)
    can_vote = bool(attendance) and user.can_vote

    # Handle voting
    if request.method == 'POST' and ('vote_choice' in request.POST or 'vote_choices' in request.POST) and can_vote:
        password = request.POST.get('password')

        if user.check_password(password):
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
                if legislation.anonymous_vote:
                    _vote_desc = f'{user.name} cast {len(vote_choices)} vote(s) on "{legislation.title}" (anonymous)'
                    _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': True, 'choices_count': len(vote_choices)}
                else:
                    _vote_desc = f'{user.name} voted for {vote_choices} on "{legislation.title}"'
                    _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': False, 'vote_choices': vote_choices}
                ActivityLog.log_activity(
                    action_type='vote_cast',
                    user=user,
                    description=_vote_desc,
                    request=request,
                    object_type='Legislation',
                    object_id=legislation.id,
                    object_repr=legislation.title,
                    metadata=_vote_meta,
                )
                messages.success(request, f"Your {len(vote_choices)} vote(s) have been submitted.")
            else:
                # Single-select voting (percentage, piecewise, or plurality with 1 vote)
                vote_choice = request.POST.get('vote_choice')
                if not vote_choice:
                    messages.error(request, "Please select a vote option.")
                    return redirect('vote')
                if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                    messages.error(request, "Invalid vote option.")
                    return redirect('vote')
                valid_standard = ['yes', 'no', 'abstain']
                if legislation.vote_mode != 'plurality' and vote_choice not in valid_standard:
                    messages.error(request, "Invalid vote option.")
                    return redirect('vote')

                Vote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)
                logger.info(f"{user.username} voted '{vote_choice}' on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
                if legislation.anonymous_vote:
                    _vote_desc = f'{user.name} cast a vote on "{legislation.title}" (anonymous)'
                    _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': True}
                else:
                    _vote_desc = f'{user.name} voted "{vote_choice}" on "{legislation.title}"'
                    _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': False, 'vote_choice': vote_choice}
                ActivityLog.log_activity(
                    action_type='vote_cast',
                    user=user,
                    description=_vote_desc,
                    request=request,
                    object_type='Legislation',
                    object_id=legislation.id,
                    object_repr=legislation.title,
                    metadata=_vote_meta,
                )
                messages.success(request, "Your vote has been submitted.")

            return redirect('vote')
        else:
            messages.error(request, "Incorrect password.")
            return redirect('vote')

    # Auto-close any chapter legislation that has passed its voting_ends_at time
    from django.db.models import Q
    _logger = logging.getLogger('function_calls')
    now = timezone.now()
    expired_legislation = Legislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now
    )
    for leg in expired_legislation:
        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        total = yes + no
        if total > 0:
            if leg.vote_mode == 'piecewise':
                leg.passed = yes >= (leg.required_number or 0)
            elif leg.vote_mode == 'plurality':
                options = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
                leg.passed = max(options.values()) > 0 if options else False
            else:
                yes_pct = (yes / total) * 100
                leg.passed = yes_pct >= int(leg.required_percentage)
            leg.status = 'passed' if leg.passed else 'failed'
        leg.save()
        _logger.info(f"Auto-closed voting on '{leg.title}' (ID: {leg.id}) - scheduled end time reached")

    # Gather available legislation
    # Show legislation that is available OR pending legislation created by the current user
    # Exclude tabled, passed, failed, and removed legislation
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

    from src.models import Role
    appointment_roles = Role.objects.all().order_by('name')
    appointment_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # Build attendance panel for officers
    members_attendance = None
    attendance_present_count = 0
    if user.member_type in ['Chair', 'Officer']:
        all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')
        recent_atts = {}
        for att in Attendance.objects.filter(
            user__in=all_members,
            created_at__gte=three_hours_ago
        ).order_by('created_at'):  # ascending so later records win
            recent_atts[att.user_id] = att.status
        members_attendance = [
            {'member': m, 'status': recent_atts.get(m.user_id, 'absent')}
            for m in all_members
        ]
        attendance_present_count = sum(
            1 for m in members_attendance if m['status'] in ('present', 'late')
        )

    return render(request, 'vote.html', {
        'profile': user,
        'can_vote': can_vote,
        'legislation': available_legislation,
        'vote_data': vote_data,
        'default_vote_mode': 'percentage',
        'appointment_roles': appointment_roles,
        'appointment_members': appointment_members,
        'members_attendance': members_attendance,
        'attendance_present_count': attendance_present_count,
    })


@login_required
def vote_tally_json(request):
    """
    JSON endpoint polled by vote.html to refresh live vote tallies.
    Returns tallies only for legislation the current user posted (matching the
    template restriction), plus a closed/open flag for each piece of legislation
    so the page can react if a vote ends while the user is watching.
    """
    user = request.user
    from django.db.models import Q

    active_legislation = Legislation.objects.filter(
        Q(available_at__lte=timezone.now()) | Q(posted_by=user),
        voting_closed=False,
    ).exclude(status__in=['pending', 'tabled', 'passed', 'failed', 'removed'])

    tallies = {}
    for leg in active_legislation:
        if leg.posted_by != user:
            # Non-authors only get the closed flag (for page reload trigger)
            tallies[leg.id] = {'closed': False}
            continue

        votes = Vote.objects.filter(legislation=leg)
        if leg.vote_mode == 'plurality':
            tally = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
            tally['total'] = votes.count()
        else:
            tally = {
                'yes': votes.filter(vote_choice='yes').count(),
                'no': votes.filter(vote_choice='no').count(),
                'abstain': votes.filter(vote_choice='abstain').count(),
                'total': votes.count(),
            }
        tally['closed'] = False
        tallies[leg.id] = tally

    # Also include any legislation that just closed since last poll so the
    # page knows to reload
    recently_closed = Legislation.objects.filter(
        posted_by=user,
        voting_closed=True,
        voting_ended_at__gte=timezone.now() - timedelta(minutes=2),
    )
    for leg in recently_closed:
        tallies[leg.id] = {'closed': True}

    return JsonResponse({'tallies': tallies})
