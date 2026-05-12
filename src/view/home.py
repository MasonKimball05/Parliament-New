from ..decorators import *
from ..models import *
from django.db.models import Count, Q
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from src.feature_flag_decorators import require_page_enabled

@login_required
@require_page_enabled('home')
@log_function_call
def home(request):
    logger.info(f"User: {request.user} | Authenticated: {request.user.is_authenticated} | IP: {request.META.get('REMOTE_ADDR')} | Page accessed: home")

    now = timezone.now()
    week_ago = now - timedelta(days=7)

    # === STATISTICS ===
    # Total active members
    total_active_members = ParliamentUser.objects.filter(member_status='Active').count()

    # Active legislation count
    active_legislation = Legislation.objects.filter(
        voting_closed=False,
        status='active'
    ).count()

    # Upcoming events (next 7 days)
    upcoming_events_count = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now,
        date_time__lte=now + timedelta(days=7)
    ).count()

    # Your active committees
    user_committees = Committee.objects.filter(
        Q(members=request.user) |
        Q(chairs=request.user) |
        Q(advisors=request.user)
    ).distinct()

    # === YOUR PENDING VOTES ===
    # Get legislation user hasn't voted on yet
    voted_legislation_ids = Vote.objects.filter(user=request.user).values_list('legislation_id', flat=True)
    pending_votes = Legislation.objects.filter(
        voting_closed=False,
        status='active'
    ).exclude(id__in=voted_legislation_ids).order_by('-available_at')[:5]

    # === UPCOMING EVENTS ===
    # Get next 3 upcoming events
    all_upcoming_events = Event.objects.filter(
        is_active=True,
        archived=False,
        date_time__gte=now
    ).order_by('date_time')
    # Filter by visibility
    upcoming_events = [e for e in all_upcoming_events if e.is_visible_to_user(request.user)][:3]

    # === RECENT ANNOUNCEMENTS ===
    all_announcements = Announcement.objects.filter(
        is_active=True
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).order_by('-posted_at')
    # Filter by visibility
    announcements = [a for a in all_announcements if a.is_visible_to_user(request.user)][:3]

    # === RECENTLY PASSED LEGISLATION ===
    recently_passed_legislation = Legislation.objects.annotate(
        total_votes=Count('vote'),
        yes_votes=Count('vote', filter=Q(vote__vote_choice='yes'))
    ).filter(
        voting_closed=True,
        status='passed'
    ).order_by('-voting_ended_at')[:3]

    legislation_previews = []
    for leg in recently_passed_legislation:
        # Use historical counts if set (manually entered legislation)
        yes = leg.historical_yes_votes if leg.historical_yes_votes is not None else leg.yes_votes
        total = leg.total_votes

        if leg.vote_mode == 'plurality':
            votes = Vote.objects.filter(legislation=leg)
            option_counts = {
                opt: votes.filter(vote_choice=opt).count()
                for opt in (leg.plurality_options or [])
            }
            winner = max(option_counts, key=option_counts.get) if option_counts else None
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'plurality',
                'winner': winner,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })
        elif leg.vote_mode == 'piecewise':
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'piecewise',
                'yes_votes': yes,
                'required_yes_votes': leg.required_number,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })
        else:
            # Percentage mode
            no = leg.historical_no_votes if leg.historical_no_votes is not None else (
                Vote.objects.filter(legislation=leg, vote_choice='no').count()
            )
            countable = yes + no
            yes_pct_str = "{:.0f}%".format((yes / countable) * 100) if countable > 0 else "N/A"
            legislation_previews.append({
                'title': leg.title,
                'vote_mode': 'percentage',
                'yes_percentage': yes_pct_str,
                'yes_pct_num': round((yes / countable) * 100) if countable > 0 else 0,
                'total_votes': total,
                'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk}),
            })

    # === RECENT ACTIVITY ===
    # Count new items this week
    new_announcements_week = len([a for a in Announcement.objects.filter(
        is_active=True,
        posted_at__gte=week_ago
    ) if a.is_visible_to_user(request.user)])

    new_events_week = len([e for e in Event.objects.filter(
        is_active=True,
        created_at__gte=week_ago
    ) if e.is_visible_to_user(request.user)])

    # === ACTIVE SLATING PERIOD ===
    # Show card if there's an active slating period (nominations or voting open, or results published)
    active_slating_period = SlatingPeriod.objects.filter(
        status__in=['nominations_open', 'voting_open', 'results_published']
    ).first()

    # Check if user has access to slating committee
    slating_committee = Committee.objects.filter(is_slating_committee=True).first()
    has_slating_access = False
    if slating_committee and not request.user.is_pledge:
        has_slating_access = (
            request.user.is_admin or
            slating_committee.admin == request.user or
            slating_committee.members.filter(pk=request.user.pk).exists() or
            slating_committee.chairs.filter(pk=request.user.pk).exists()
        )

    # Show slating card if user has committee access OR there's an active period
    show_slating_card = has_slating_access or (active_slating_period and not request.user.is_pledge)

    # Enhanced slating data for rich card
    slating_positions = []
    slating_total_applications = 0
    user_has_applied = False
    user_has_voted = False
    slating_passed_slate = None
    slating_slate_candidates = []

    if active_slating_period:
        slating_positions = list(
            active_slating_period.positions.filter(is_active=True).order_by('display_order', 'title')
        )
        if active_slating_period.status == 'nominations_open':
            slating_total_applications = active_slating_period.applications.exclude(
                status='withdrawn'
            ).count()
            user_has_applied = active_slating_period.applications.filter(
                applicant=request.user
            ).exclude(status='withdrawn').exists()
        elif active_slating_period.status == 'voting_open':
            user_has_voted = SlatingBallot.objects.filter(
                period=active_slating_period,
                voter=request.user,
            ).exists()
        elif active_slating_period.status == 'results_published':
            slating_passed_slate = active_slating_period.slates.filter(passed=True).first()
            if slating_passed_slate:
                slating_slate_candidates = list(
                    slating_passed_slate.candidates.select_related(
                        'position', 'application__applicant'
                    ).order_by('display_order')
                )

    context = {
        'user': request.user,
        # Stats
        'total_active_members': total_active_members,
        'active_legislation': active_legislation,
        'upcoming_events_count': upcoming_events_count,
        'user_committees': user_committees,
        'user_committees_count': user_committees.count(),
        # Content
        'pending_votes': pending_votes,
        'upcoming_events': upcoming_events,
        'announcements': announcements,
        'legislation_previews': legislation_previews,
        # Activity
        'new_announcements_week': new_announcements_week,
        'new_events_week': new_events_week,
        # Slating
        'active_slating_period': active_slating_period,
        'has_slating_access': has_slating_access,
        'show_slating_card': show_slating_card,
        'slating_positions': slating_positions,
        'slating_total_applications': slating_total_applications,
        'user_has_applied': user_has_applied,
        'user_has_voted': user_has_voted,
        'slating_passed_slate': slating_passed_slate,
        'slating_slate_candidates': slating_slate_candidates,
    }

    layout = getattr(request.user.preferences, 'home_layout', 'modern')
    template = 'home_classic.html' if layout == 'classic' else 'home_modern.html'
    return render(request, template, context)