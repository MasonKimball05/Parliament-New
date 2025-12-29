from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import Announcement
from datetime import datetime, timedelta
from django.utils import timezone

@login_required
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

    return render(request, 'announcements.html', {
        'announcements': announcements,
        'search_query': search_query,
        'days_filter': days_filter,
    })
