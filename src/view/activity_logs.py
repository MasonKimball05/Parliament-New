"""
Activity logs view for officers
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.core.paginator import Paginator
from src.models import ActivityLog, ParliamentUser
from ..decorators import *
from django.db.models import Q
from datetime import datetime, timedelta
from django.utils import timezone
from src.utils.export_utils import export_to_csv


@login_required
@officer_required
def activity_logs_view(request):
    """
    View for officers to see comprehensive activity logs with filtering
    """
    # Get filter parameters
    action_category = request.GET.get('category', '')
    action_type = request.GET.get('type', '')
    user_filter = request.GET.get('user', '')
    search_query = request.GET.get('q', '')
    date_range = request.GET.get('date_range', '7')  # Default to last 7 days

    # Start with all logs
    logs = ActivityLog.objects.all().select_related('user')

    # Apply date range filter
    now = timezone.now()
    if date_range == '1':
        start_date = now - timedelta(days=1)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '7':
        start_date = now - timedelta(days=7)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '30':
        start_date = now - timedelta(days=30)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '90':
        start_date = now - timedelta(days=90)
        logs = logs.filter(timestamp__gte=start_date)
    # 'all' shows everything

    # Apply category filter
    if action_category:
        logs = logs.filter(action_category=action_category)

    # Apply action type filter
    if action_type:
        logs = logs.filter(action_type=action_type)

    # Apply user filter
    if user_filter:
        logs = logs.filter(user__user_id=user_filter)

    # Apply search filter
    if search_query:
        logs = logs.filter(
            Q(description__icontains=search_query) |
            Q(user__name__icontains=search_query) |
            Q(object_repr__icontains=search_query) |
            Q(ip_address__icontains=search_query)
        )

    # Pagination
    paginator = Paginator(logs, 50)  # Show 50 logs per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Get unique users for filter dropdown
    active_users = ParliamentUser.objects.filter(
        member_status='Active'
    ).order_by('name')

    # Get statistics
    total_logs = logs.count()
    unique_users = logs.values('user').distinct().count()

    # Get category counts for the filtered results
    category_counts = {}
    for category_code, category_name in ActivityLog.ACTION_CATEGORIES:
        count = logs.filter(action_category=category_code).count()
        if count > 0:
            category_counts[category_code] = {
                'name': category_name,
                'count': count
            }

    context = {
        'page_obj': page_obj,
        'active_users': active_users,
        'action_categories': ActivityLog.ACTION_CATEGORIES,
        'action_types': ActivityLog.ACTION_TYPES,
        'category_counts': category_counts,
        'total_logs': total_logs,
        'unique_users': unique_users,
        # Filters
        'selected_category': action_category,
        'selected_type': action_type,
        'selected_user': user_filter,
        'search_query': search_query,
        'date_range': date_range,
    }

    return render(request, 'activity_logs.html', context)


@login_required
@officer_required
def export_activity_logs(request):
    """
    Export activity logs to CSV with applied filters
    """
    # Get filter parameters (same as main view)
    action_category = request.GET.get('category', '')
    action_type = request.GET.get('type', '')
    user_filter = request.GET.get('user', '')
    search_query = request.GET.get('q', '')
    date_range = request.GET.get('date_range', '7')

    # Apply same filters as the main view
    logs = ActivityLog.objects.all().select_related('user')

    # Apply date range filter
    now = timezone.now()
    if date_range == '1':
        start_date = now - timedelta(days=1)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '7':
        start_date = now - timedelta(days=7)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '30':
        start_date = now - timedelta(days=30)
        logs = logs.filter(timestamp__gte=start_date)
    elif date_range == '90':
        start_date = now - timedelta(days=90)
        logs = logs.filter(timestamp__gte=start_date)

    if action_category:
        logs = logs.filter(action_category=action_category)

    if action_type:
        logs = logs.filter(action_type=action_type)

    if user_filter:
        logs = logs.filter(user__user_id=user_filter)

    if search_query:
        logs = logs.filter(
            Q(description__icontains=search_query) |
            Q(user__name__icontains=search_query) |
            Q(object_repr__icontains=search_query) |
            Q(ip_address__icontains=search_query)
        )

    # Prepare CSV data
    headers = [
        'Timestamp',
        'User',
        'User ID',
        'Action Category',
        'Action Type',
        'Description',
        'Object Type',
        'Object ID',
        'Object Name',
        'IP Address',
    ]

    rows = []
    for log in logs:
        rows.append([
            log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            log.user.get_display_name() if log.user else 'System',
            log.user.user_id if log.user else 'N/A',
            log.get_action_category_display(),
            log.get_action_type_display(),
            log.description,
            log.object_type,
            log.object_id if log.object_id else '',
            log.object_repr,
            log.ip_address if log.ip_address else '',
        ])

    # Log the export
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported {len(rows)} activity log entries to CSV',
        request=request,
        metadata={'record_count': len(rows), 'filters': {
            'category': action_category,
            'type': action_type,
            'user': user_filter,
            'date_range': date_range
        }}
    )

    return export_to_csv('activity_logs', headers, rows)
