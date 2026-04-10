from django.contrib.auth.decorators import login_required
from src.models import *
from django.shortcuts import render
from src.decorators import officer_required
from src.utils.export_utils import export_to_csv
from src.feature_flag_decorators import require_page_enabled
from django.utils import timezone
from django.utils.timezone import localtime
from datetime import timedelta

@login_required
@officer_required
@require_page_enabled('user_list')
def user_list(request):
    # Get filter parameters
    member_status_filter = request.GET.get('status', 'active')
    member_type_filter = request.GET.get('type', '')
    search_query = request.GET.get('q', '')
    sort_by = request.GET.get('sort', 'name')
    sort_order = request.GET.get('order', 'asc')

    # Start with all users
    users = ParliamentUser.objects.all().prefetch_related('roles')

    # Apply status filter - default to Active and Advisors
    if member_status_filter == 'active':
        users = users.filter(member_status='Active') | users.filter(member_type='Advisor')
    elif member_status_filter == 'inactive':
        users = users.filter(member_status='Inactive')
    elif member_status_filter == 'alumni':
        users = users.filter(member_status='Alumni')
    elif member_status_filter == 'removed':
        users = users.filter(member_status='Removed')
    else:
        # 'all' shows everyone except Removed
        users = users.exclude(member_status='Removed')

    # Apply member type filter
    if member_type_filter:
        users = users.filter(member_type=member_type_filter)

    # Apply search filter
    if search_query:
        users = users.filter(
            models.Q(name__icontains=search_query) |
            models.Q(user_id__icontains=search_query) |
            models.Q(email__icontains=search_query) |
            models.Q(preferred_name__icontains=search_query) |
            models.Q(role_number__icontains=search_query)
        )

    # Apply sorting (for database fields only, role_count handled below)
    valid_sort_fields = {
        'name': 'name',
        'id': 'user_id',
        'email': 'email',
        'type': 'member_type',
        'status': 'member_status',
        'last_login': 'last_login',
    }

    # Sort by database field first if not sorting by role_count
    if sort_by != 'role_count':
        sort_field = valid_sort_fields.get(sort_by, 'name')
        if sort_order == 'desc':
            sort_field = f'-{sort_field}'
        users = users.order_by(sort_field)
    else:
        # For role_count, we'll need to do it in Python after fetching
        users = users.order_by('name')  # Default order while we fetch

    # Calculate enhanced user data
    now = timezone.now()
    user_data = []

    for user in users:
        # Calculate days since last login
        if user.last_login:
            days_ago = (now - user.last_login).days
            local_last_login = localtime(user.last_login)
            if days_ago == 0:
                last_login_display = local_last_login.strftime('%b %d, %Y') + ': Today'
            elif days_ago == 1:
                last_login_display = local_last_login.strftime('%b %d, %Y') + ': 1 day ago'
            else:
                last_login_display = local_last_login.strftime('%b %d, %Y') + f': {days_ago} days ago'
        else:
            last_login_display = 'Never logged in'

        # Get roles
        role_list = list(user.roles.all())
        role_count = len(role_list)

        user_data.append({
            'user': user,
            'username': user.username,
            'id': user.user_id,
            'role_number': user.role_number,  # Member roll number (assigned at initiation)
            'email': user.email or 'No email',
            'role': user.member_type,
            'member_status': user.member_status,
            'last_login': last_login_display,
            'role_count': role_count,
            'roles': role_list[:3],  # Show up to 3 roles
            'has_more_roles': role_count > 3,
        })

    # Sort by role_count if requested (must be done after building user_data)
    if sort_by == 'role_count':
        user_data.sort(key=lambda x: x['role_count'], reverse=(sort_order == 'desc'))

    # Check if any pledges exist in the current view
    has_pledges = any(data['role'] == 'Pledge' for data in user_data)

    # Get filter options
    member_types = ParliamentUser.MEMBER_TYPES
    member_statuses = ParliamentUser.MEMBER_STATUS

    context = {
        'user_data': user_data,
        'member_types': member_types,
        'member_statuses': member_statuses,
        'selected_status': member_status_filter,
        'selected_type': member_type_filter,
        'search_query': search_query,
        'total_users': len(user_data),
        'current_sort': sort_by,
        'current_order': sort_order,
        'has_pledges': has_pledges,
    }

    return render(request, 'user_list.html', context)


@login_required
@officer_required
def export_user_list(request):
    """
    Export user list to CSV
    """
    # Get filter parameter if exists
    status_filter = request.GET.get('status', 'active')

    # Get users based on filter - default to Active and Advisors
    if status_filter == 'active':
        users = ParliamentUser.objects.filter(member_status='Active') | ParliamentUser.objects.filter(member_type='Advisor')
    elif status_filter == 'inactive':
        users = ParliamentUser.objects.filter(member_status='Inactive')
    elif status_filter == 'alumni':
        users = ParliamentUser.objects.filter(member_status='Alumni')
    elif status_filter == 'removed':
        users = ParliamentUser.objects.filter(member_status='Removed')
    else:
        users = ParliamentUser.objects.exclude(member_status='Removed')

    users = users.order_by('name')

    # Prepare CSV data
    headers = [
        'Name',
        'Roll Number',
        'User ID',
        'Email',
        'Member Type',
        'Member Status',
        'Last Login',
        'Role Count',
    ]

    rows = []
    now = timezone.now()
    for user in users.prefetch_related('roles'):
        # Calculate last login
        if user.last_login:
            last_login_str = localtime(user.last_login).strftime('%Y-%m-%d')
        else:
            last_login_str = 'Never'

        rows.append([
            user.name,
            user.role_number if user.role_number else '',
            user.user_id,
            user.email if user.email else '',
            user.member_type,
            user.member_status,
            last_login_str,
            user.roles.count(),
        ])

    # Log the export
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} exported {len(rows)} users to CSV',
        request=request,
        metadata={'record_count': len(rows), 'status_filter': status_filter}
    )

    return export_to_csv('user_list', headers, rows)