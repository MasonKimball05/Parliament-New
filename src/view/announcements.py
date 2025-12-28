from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import Announcement
from datetime import datetime, timedelta
from django.utils import timezone

@login_required
def announcements_view(request):
    """View all announcements from the past year"""
    from django.db.models import Q
    # Get announcements from the past year
    one_year_ago = timezone.now() - timedelta(days=365)
    now = timezone.now()
    all_announcements = Announcement.objects.filter(
        is_active=True,
        posted_at__gte=one_year_ago
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).order_by('-posted_at')

    # Filter by visibility - only show announcements visible to this user
    announcements = [a for a in all_announcements if a.is_visible_to_user(request.user)]

    return render(request, 'announcements.html', {
        'announcements': announcements,
    })
