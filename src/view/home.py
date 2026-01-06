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
    print(f"🔐 User: {request.user} | Authenticated: {request.user.is_authenticated}")
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

    legislation_previews = [
        {
            'title': leg.title,
            'yes_percentage': "{:.0%}".format(leg.yes_votes / leg.total_votes) if leg.total_votes > 0 else "0%",
            'total_votes': leg.total_votes,
            'detail_url': reverse('passed_legislation_detail', kwargs={'pk': leg.pk})
        } for leg in recently_passed_legislation
    ]

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
    }

    return render(request, 'home.html', context)