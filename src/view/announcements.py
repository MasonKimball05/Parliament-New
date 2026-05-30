from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import Announcement, UserAnnouncementView
from datetime import datetime, timedelta
from django.utils import timezone
from src.feature_flag_decorators import require_feature_flag, require_page_enabled
from src.models import AnnouncementPollResponse

@login_required
@require_feature_flag('announcements')
@require_page_enabled('announcements')
def announcements_view(request):
    """View all announcements from the past year with filtering and search"""
    from django.db.models import Q

    # Get filter parameters
    search_query = request.GET.get('q', '').strip()
    days_filter = request.GET.get('days', 'all')

    # Base query
    now = timezone.now()
    base_query = Announcement.objects.filter(
        is_active=True
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    )

    # Apply date filter
    if days_filter == '7':
        start_date = now - timedelta(days=7)
        base_query = base_query.filter(posted_at__gte=start_date)
    elif days_filter == '30':
        start_date = now - timedelta(days=30)
        base_query = base_query.filter(posted_at__gte=start_date)
    elif days_filter == '90':
        start_date = now - timedelta(days=90)
        base_query = base_query.filter(posted_at__gte=start_date)
    else:  # 'all' - default to past year
        one_year_ago = now - timedelta(days=365)
        base_query = base_query.filter(posted_at__gte=one_year_ago)

    # Apply search filter
    if search_query:
        base_query = base_query.filter(
            Q(title__icontains=search_query) | Q(content__icontains=search_query)
        )

    # Order by date
    all_announcements = base_query.order_by('-posted_at')

    # Filter by visibility - only show announcements visible to this user
    announcements = [a for a in all_announcements if a.is_visible_to_user(request.user)]

    # Track site views for visible announcements
    for announcement in announcements:
        UserAnnouncementView.objects.get_or_create(
            user=request.user,
            announcement=announcement,
            defaults={'view_source': 'site'}
        )

    # Precompute poll response state for each announcement that has a poll
    responded_poll_ids = set(
        AnnouncementPollResponse.objects.filter(
            respondent=request.user,
            poll__announcement__in=announcements,
        ).values_list('poll__announcement_id', flat=True)
    )

    return render(request, 'announcements.html', {
        'announcements': announcements,
        'search_query': search_query,
        'days_filter': days_filter,
        'responded_poll_ids': responded_poll_ids,
    })
