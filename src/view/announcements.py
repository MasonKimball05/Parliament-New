from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import Announcement, UserAnnouncementView
from datetime import datetime, timedelta
from django.utils import timezone
from src.feature_flag_decorators import require_feature_flag, require_page_enabled
from src.models import AnnouncementPollResponse
from src.models.users import member_defer
from src.utils.visibility import visible_to_q

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

    # Order by date.
    #
    # v3.17.3 (second pass): three fixes on one queryset.
    #  * Visibility is filtered in SQL (`visible_to_q`) rather than by walking
    #    a year of announcements in Python.
    #  * `posted_by` is joined — the template prints the author's name per row
    #    (announcements.html:169), so this was a member fetch per announcement.
    #  * The view-tracking loop below was one get_or_create per announcement.
    announcements = list(
        base_query
        .filter(visible_to_q(request.user.member_type))
        # `poll` is the reverse side of a OneToOneField, so select_related
        # handles it — the template checks `{% if announcement.poll %}` on
        # every row (announcements.html:126), which was a query per
        # announcement whether or not a poll existed.
        .select_related('posted_by', 'poll')
        # announcements.html:148 iterates `announcement.linked_documents.all`
        # per row — a many-to-many, so it needs a prefetch rather than a join.
        .prefetch_related('linked_documents')
        .defer(*member_defer('posted_by'))
        .order_by('-posted_at')
    )

    # Track site views for visible announcements.
    #
    # v3.17.3: was `get_or_create` inside the loop — a SELECT per announcement
    # (plus an INSERT for each new one) on every load of this page. Now one
    # SELECT for the rows that already exist and one bulk INSERT for the rest.
    # `ignore_conflicts` covers the race with a concurrent tab, which is what
    # get_or_create's own IntegrityError branch was doing; `unique_together`
    # on (user, announcement) is what makes that safe.
    if announcements:
        already_seen = set(
            UserAnnouncementView.objects
            .filter(user=request.user, announcement__in=announcements)
            .values_list('announcement_id', flat=True)
        )
        missing = [
            UserAnnouncementView(
                user=request.user, announcement=a, view_source='site')
            for a in announcements if a.pk not in already_seen
        ]
        if missing:
            UserAnnouncementView.objects.bulk_create(missing, ignore_conflicts=True)

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
