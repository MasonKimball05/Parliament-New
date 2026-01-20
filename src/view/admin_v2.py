"""
Admin v2 - Advanced administrative interface
Requires dual authentication: user password + secret key
"""
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate
from django.contrib import messages
from django.conf import settings
from django.utils import timezone
from django.db.models import Count, Q
from datetime import datetime, timedelta
from src.models_feature_flags import FeatureFlag, PageToggle, SiteSetting
from src.models import (
    ParliamentUser, Legislation, Event, Committee,
    Announcement, ActivityLog, LoginHistory, LoginAlert,
    IPWhitelist, IPBlacklist
)
import os
import secrets
import string
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from src.logging_utils import get_client_ip


ALLOWED_USER_ID = '73'  # Your user ID


def generate_random_password(length=16):
    """Generate a secure random password"""
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return ''.join(secrets.choice(alphabet) for i in range(length))


def admin_v2_login(request):
    """
    Login page for Admin v2 - requires user password + secret key
    """
    # Check if user is authenticated
    if not request.user.is_authenticated:
        messages.warning(request, 'Please login first to access Admin v2')
        return redirect('login')

    if request.method == 'POST':
        user_password = request.POST.get('user_password', '')
        secret_key = request.POST.get('secret_key', '')

        # Check if user is the authorized user (user_id 73)
        if not hasattr(request.user, 'user_id') or request.user.user_id != ALLOWED_USER_ID:
            messages.error(request, 'Unauthorized access attempt. This incident has been logged.')
            ActivityLog.log_activity(
                action_type='security_violation',
                user=request.user,
                description=f'Unauthorized Admin v2 access attempt by {request.user.get_display_name()}',
                request=request
            )
            return redirect('home')

        # Verify user password
        user = authenticate(username=request.user.username, password=user_password)
        if user is None:
            messages.error(request, 'Invalid user password')
            return render(request, 'admin_v2/login.html')

        # Verify secret key from environment
        env_secret = os.environ.get('ADMIN_V2_SECRET_KEY', '')
        if not env_secret:
            messages.error(request, 'Admin v2 secret key not configured. Contact system administrator.')
            return render(request, 'admin_v2/login.html')

        if secret_key != env_secret:
            messages.error(request, 'Invalid secret key')
            ActivityLog.log_activity(
                action_type='security_violation',
                user=request.user,
                description=f'Failed Admin v2 secret key attempt by {request.user.get_display_name()}',
                request=request
            )
            return render(request, 'admin_v2/login.html')

        # Both passwords correct - grant access
        request.session['admin_v2_authenticated'] = True
        request.session['admin_v2_auth_time'] = timezone.now().isoformat()

        ActivityLog.log_activity(
            action_type='admin_v2_access',
            user=request.user,
            description=f'{request.user.get_display_name()} successfully accessed Admin v2',
            request=request
        )

        messages.success(request, 'Admin v2 access granted')
        return redirect('admin_v2_dashboard')

    return render(request, 'admin_v2/login.html')


def require_admin_v2_auth(view_func):
    """
    Decorator to require Admin v2 authentication
    """
    def wrapper(request, *args, **kwargs):
        # Check if user is authenticated
        if not request.user.is_authenticated:
            messages.warning(request, 'Please login to access Admin v2')
            return redirect('login')

        # Check if user is authorized
        if not hasattr(request.user, 'user_id') or request.user.user_id != ALLOWED_USER_ID:
            messages.error(request, 'Unauthorized access')
            return redirect('home')

        # Check if Admin v2 session is active
        if not request.session.get('admin_v2_authenticated'):
            messages.warning(request, 'Please authenticate to access Admin v2')
            return redirect('admin_v2_login')

        return view_func(request, *args, **kwargs)
    return wrapper


@require_admin_v2_auth
def admin_v2_dashboard(request):
    """
    Main Admin v2 dashboard showing site statistics and controls
    """
    from src.models import CommitteeDocument, Vote, CommitteeVote
    from django.db import connection

    # Gather comprehensive site statistics
    stats = {
        'users': {
            'total': ParliamentUser.objects.count(),
            'active': ParliamentUser.objects.filter(member_status='Active').count(),
            'inactive': ParliamentUser.objects.filter(member_status='Inactive').count(),
            'alumni': ParliamentUser.objects.filter(member_status='Alumni').count(),
            'officers': ParliamentUser.objects.filter(member_type='Officer').count(),
            'members': ParliamentUser.objects.filter(member_type='Member').count(),
            'pledges': ParliamentUser.objects.filter(member_type='Pledge').count(),
            'advisors': ParliamentUser.objects.filter(member_type='Advisor').count(),
            'admins': ParliamentUser.objects.filter(is_admin=True).count(),
            'last_24h': ParliamentUser.objects.filter(last_login__gte=timezone.now() - timezone.timedelta(hours=24)).count(),
            'never_logged_in': ParliamentUser.objects.filter(last_login__isnull=True).count(),
        },
        'legislation': {
            'total': Legislation.objects.count(),
            'draft': Legislation.objects.filter(status='draft').count(),
            'passed': Legislation.objects.filter(status='passed').count(),
            'removed': Legislation.objects.filter(status='removed').count(),
            'voting_closed': Legislation.objects.filter(voting_closed=True).count(),
            'total_votes': Vote.objects.count(),
            'recent_votes': 0,  # Vote model doesn't have timestamp field
        },
        'events': {
            'total': Event.objects.count(),
            'upcoming': Event.objects.filter(date_time__gte=timezone.now(), is_active=True).count(),
            'past': Event.objects.filter(date_time__lt=timezone.now()).count(),
            'archived': Event.objects.filter(archived=True).count(),
            'this_month': Event.objects.filter(
                date_time__gte=timezone.now().replace(day=1, hour=0, minute=0, second=0),
                date_time__lt=(timezone.now().replace(day=1, hour=0, minute=0, second=0) + timezone.timedelta(days=32)).replace(day=1)
            ).count(),
        },
        'committees': {
            'total': Committee.objects.count(),
            'active': Committee.objects.filter(is_active=True).count(),
            'inactive': Committee.objects.filter(is_active=False).count(),
            'with_members': Committee.objects.annotate(member_count=Count('members')).filter(member_count__gt=0).count(),
            'total_documents': CommitteeDocument.objects.count(),
            'published_docs': CommitteeDocument.objects.filter(published_to_chapter=True).count(),
            'total_committee_votes': CommitteeVote.objects.count(),
        },
        'announcements': {
            'total': Announcement.objects.count(),
            'active': Announcement.objects.filter(is_active=True).count(),
            'inactive': Announcement.objects.filter(is_active=False).count(),
        },
        'communications': {
            'total_channels': 0,  # Channel model not yet implemented
            'total_activity_logs': ActivityLog.objects.count(),
            'logs_last_24h': ActivityLog.objects.filter(timestamp__gte=timezone.now() - timezone.timedelta(hours=24)).count(),
            'logs_last_7d': ActivityLog.objects.filter(timestamp__gte=timezone.now() - timezone.timedelta(days=7)).count(),
        },
        'security': {
            'total_logins': LoginHistory.objects.count(),
            'logins_24h': LoginHistory.objects.filter(timestamp__gte=timezone.now() - timezone.timedelta(hours=24)).count(),
            'logins_7d': LoginHistory.objects.filter(timestamp__gte=timezone.now() - timezone.timedelta(days=7)).count(),
            'recent_alerts': LoginAlert.objects.filter(status='new').count(),
            'total_alerts': LoginAlert.objects.count(),
            'failed_logins_24h': LoginHistory.objects.filter(
                timestamp__gte=timezone.now() - timezone.timedelta(hours=24),
                successful=False
            ).count() if hasattr(LoginHistory, 'successful') else 0,
        },
        'database': {
            'tables': len(connection.introspection.table_names()),
        }
    }

    # Get feature flags grouped by category
    feature_flags = {}
    for category, category_name in FeatureFlag.CATEGORY_CHOICES:
        flags = FeatureFlag.objects.filter(category=category)
        if flags.exists():
            feature_flags[category_name] = flags

    # Get page toggles
    page_toggles = PageToggle.objects.all().order_by('display_name')

    # Ensure chat settings exist
    chat_settings_defaults = [
        {
            'key': 'chat_active_poll_interval',
            'display_name': 'Chat Active Poll Interval',
            'description': 'How often (in milliseconds) to poll for new messages when the page is active/visible',
            'category': 'chat',
            'setting_type': 'integer',
            'default_value': '3000',
        },
        {
            'key': 'chat_inactive_poll_interval',
            'display_name': 'Chat Inactive Poll Interval',
            'description': 'How often (in milliseconds) to poll for new messages when the page is in the background',
            'category': 'chat',
            'setting_type': 'integer',
            'default_value': '20000',
        },
        {
            'key': 'chat_active_users_poll_interval',
            'display_name': 'Active Users Poll Interval',
            'description': 'How often (in milliseconds) to update the active users list when page is active',
            'category': 'chat',
            'setting_type': 'integer',
            'default_value': '5000',
        },
    ]
    for setting_data in chat_settings_defaults:
        SiteSetting.objects.get_or_create(
            key=setting_data['key'],
            defaults={
                'display_name': setting_data['display_name'],
                'description': setting_data['description'],
                'category': setting_data['category'],
                'setting_type': setting_data['setting_type'],
                'value': setting_data['default_value'],
                'default_value': setting_data['default_value'],
            }
        )

    # Get chat settings
    chat_settings = SiteSetting.objects.filter(category='chat').order_by('display_name')

    # Recent activity logs (last 30)
    recent_logs = ActivityLog.objects.select_related('user').order_by('-timestamp')[:30]

    # Recent logins (last 20)
    recent_logins = LoginHistory.objects.select_related('user').order_by('-timestamp')[:20]

    # Recent users (last 10 created)
    recent_users = ParliamentUser.objects.order_by('-date_joined')[:10] if hasattr(ParliamentUser, 'date_joined') else []

    # System info
    import sys
    import django
    system_info = {
        'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        'django_version': django.get_version(),
        'debug_mode': settings.DEBUG,
        'database_engine': settings.DATABASES['default']['ENGINE'].split('.')[-1],
    }

    context = {
        'stats': stats,
        'feature_flags': feature_flags,
        'page_toggles': page_toggles,
        'chat_settings': chat_settings,
        'recent_logs': recent_logs,
        'recent_logins': recent_logins,
        'recent_users': recent_users,
        'system_info': system_info,
    }

    return render(request, 'admin_v2/dashboard.html', context)


@require_admin_v2_auth
def toggle_feature_flag(request, flag_id):
    """
    Toggle a feature flag on/off
    """
    if request.method == 'POST':
        try:
            flag = FeatureFlag.objects.get(id=flag_id)
            flag.is_enabled = not flag.is_enabled
            flag.last_toggled_by = request.user.get_display_name()
            flag.last_toggled_at = timezone.now()
            flag.save()

            status = "enabled" if flag.is_enabled else "disabled"
            messages.success(request, f'Feature "{flag.display_name}" has been {status}')

            ActivityLog.log_activity(
                action_type='feature_flag_toggle',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} feature: {flag.display_name}',
                request=request
            )
        except FeatureFlag.DoesNotExist:
            messages.error(request, 'Feature flag not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def toggle_page(request, toggle_id):
    """
    Toggle a page on/off
    """
    if request.method == 'POST':
        try:
            toggle = PageToggle.objects.get(id=toggle_id)
            toggle.is_enabled = not toggle.is_enabled
            toggle.last_toggled_by = request.user.get_display_name()
            toggle.last_toggled_at = timezone.now()
            toggle.save()

            status = "enabled" if toggle.is_enabled else "disabled"
            messages.success(request, f'Page "{toggle.display_name}" has been {status}')

            ActivityLog.log_activity(
                action_type='page_toggle',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} page: {toggle.display_name}',
                request=request
            )
        except PageToggle.DoesNotExist:
            messages.error(request, 'Page toggle not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def update_site_setting(request, setting_id):
    """
    Update a site setting value
    """
    if request.method == 'POST':
        try:
            setting = SiteSetting.objects.get(id=setting_id)
            new_value = request.POST.get('value', '').strip()

            # Validate based on setting type
            if setting.setting_type == 'integer':
                try:
                    int(new_value)
                except ValueError:
                    messages.error(request, f'Invalid value for {setting.display_name}. Must be a number.')
                    return redirect('admin_v2_dashboard')
            elif setting.setting_type == 'boolean':
                new_value = 'true' if new_value.lower() in ('true', '1', 'yes', 'on') else 'false'

            old_value = setting.value
            setting.value = new_value
            setting.last_modified_by = request.user.get_display_name()
            setting.save()

            messages.success(request, f'Setting "{setting.display_name}" updated to {new_value}')

            ActivityLog.log_activity(
                action_type='setting_change',
                user=request.user,
                description=f'{request.user.get_display_name()} changed {setting.display_name} from {old_value} to {new_value}',
                request=request
            )
        except SiteSetting.DoesNotExist:
            messages.error(request, 'Setting not found')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def admin_v2_logout(request):
    """
    Logout from Admin v2
    """
    request.session.pop('admin_v2_authenticated', None)
    request.session.pop('admin_v2_auth_time', None)

    ActivityLog.log_activity(
        action_type='admin_v2_logout',
        user=request.user,
        description=f'{request.user.get_display_name()} logged out of Admin v2',
        request=request
    )

    messages.success(request, 'Logged out of Admin v2')
    return redirect('home')


# ===== MANAGEMENT VIEWS =====

@require_admin_v2_auth
def manage_legislation(request):
    """
    Manage all legislation with filtering, editing, and deletion
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    search_query = request.GET.get('search', '')

    # Build query
    legislation_list = Legislation.objects.select_related('posted_by').order_by('-created_at')

    if status_filter:
        legislation_list = legislation_list.filter(status=status_filter)

    if search_query:
        legislation_list = legislation_list.filter(
            Q(title__icontains=search_query) |
            Q(posted_by__first_name__icontains=search_query) |
            Q(posted_by__last_name__icontains=search_query)
        )

    # Paginate
    paginator = Paginator(legislation_list, 25)
    page_number = request.GET.get('page')
    legislation = paginator.get_page(page_number)

    context = {
        'legislation': legislation,
        'status_filter': status_filter,
        'search_query': search_query,
        'status_choices': ['draft', 'passed', 'removed'],
    }

    return render(request, 'admin_v2/manage_legislation.html', context)


@require_admin_v2_auth
def delete_legislation(request, legislation_id):
    """
    Delete a piece of legislation
    """
    if request.method == 'POST':
        try:
            legislation = Legislation.objects.get(id=legislation_id)
            title = legislation.title
            legislation.delete()

            ActivityLog.log_activity(
                action_type='legislation_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted legislation: {title}',
                request=request
            )

            messages.success(request, f'Legislation "{title}" has been deleted')
        except Legislation.DoesNotExist:
            messages.error(request, 'Legislation not found')

    return redirect('admin_v2_manage_legislation')


@require_admin_v2_auth
def manage_events(request):
    """
    Manage all events with filtering and editing
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    archived_filter = request.GET.get('archived', '')
    active_filter = request.GET.get('active', '')
    search_query = request.GET.get('search', '')

    # Build query
    events_list = Event.objects.order_by('-date_time')

    if archived_filter == 'yes':
        events_list = events_list.filter(archived=True)
    elif archived_filter == 'no':
        events_list = events_list.filter(archived=False)

    if active_filter == 'yes':
        events_list = events_list.filter(is_active=True)
    elif active_filter == 'no':
        events_list = events_list.filter(is_active=False)

    if search_query:
        events_list = events_list.filter(Q(title__icontains=search_query) | Q(description__icontains=search_query))

    # Paginate
    paginator = Paginator(events_list, 25)
    page_number = request.GET.get('page')
    events = paginator.get_page(page_number)

    context = {
        'events': events,
        'archived_filter': archived_filter,
        'active_filter': active_filter,
        'search_query': search_query,
    }

    return render(request, 'admin_v2/manage_events.html', context)


@require_admin_v2_auth
def delete_event(request, event_id):
    """
    Delete an event
    """
    if request.method == 'POST':
        try:
            event = Event.objects.get(id=event_id)
            title = event.title
            event.delete()

            ActivityLog.log_activity(
                action_type='event_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted event: {title}',
                request=request
            )

            messages.success(request, f'Event "{title}" has been deleted')
        except Event.DoesNotExist:
            messages.error(request, 'Event not found')

    return redirect('admin_v2_manage_events')


@require_admin_v2_auth
def manage_committees(request):
    """
    Manage all committees
    """
    committees = Committee.objects.annotate(
        member_count=Count('members'),
        chair_count=Count('chairs'),
        document_count=Count('documents')
    ).order_by('name')

    context = {
        'committees': committees,
    }

    return render(request, 'admin_v2/manage_committees.html', context)


@require_admin_v2_auth
def toggle_committee_active(request, committee_id):
    """
    Toggle committee active status
    """
    if request.method == 'POST':
        try:
            committee = Committee.objects.get(id=committee_id)
            committee.is_active = not committee.is_active
            committee.save()

            status = "activated" if committee.is_active else "deactivated"
            messages.success(request, f'Committee "{committee.name}" has been {status}')

            ActivityLog.log_activity(
                action_type='committee_status_changed',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} committee: {committee.name}',
                request=request
            )
        except Committee.DoesNotExist:
            messages.error(request, 'Committee not found')

    return redirect('admin_v2_manage_committees')


@require_admin_v2_auth
def manage_users(request):
    """
    Manage all users with filtering
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    admin_filter = request.GET.get('admin', '')
    search_query = request.GET.get('search', '')

    # Build query
    users_list = ParliamentUser.objects.order_by('name')

    if status_filter:
        users_list = users_list.filter(member_status=status_filter)

    if type_filter:
        users_list = users_list.filter(member_type=type_filter)

    if admin_filter == 'yes':
        users_list = users_list.filter(is_admin=True)
    elif admin_filter == 'no':
        users_list = users_list.filter(is_admin=False)

    if search_query:
        users_list = users_list.filter(
            Q(name__icontains=search_query) |
            Q(username__icontains=search_query) |
            Q(user_id__icontains=search_query)
        )

    # Paginate
    paginator = Paginator(users_list, 50)
    page_number = request.GET.get('page')
    users = paginator.get_page(page_number)

    context = {
        'users': users,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'admin_filter': admin_filter,
        'search_query': search_query,
    }

    return render(request, 'admin_v2/manage_users.html', context)


@require_admin_v2_auth
def toggle_user_admin(request, user_id):
    """
    Toggle user admin status
    """
    if request.method == 'POST':
        try:
            user = ParliamentUser.objects.get(user_id=user_id)
            user.is_admin = not user.is_admin
            user.save()

            status = "granted" if user.is_admin else "revoked"
            messages.success(request, f'Admin access {status} for {user.get_display_name()}')

            ActivityLog.log_activity(
                action_type='user_admin_changed',
                user=request.user,
                description=f'{request.user.get_display_name()} {status} admin access for {user.get_display_name()}',
                request=request
            )
        except ParliamentUser.DoesNotExist:
            messages.error(request, 'User not found')

    return redirect('admin_v2_manage_users')


@require_admin_v2_auth
@require_POST
def remove_user_profile_picture(request, user_id):
    """
    Remove a user's profile picture (admin-v2 action)
    """
    try:
        user = ParliamentUser.objects.get(user_id=user_id)

        if user.profile_picture:
            user.profile_picture.delete()
            user.profile_picture_removed_by_admin = True
            user.save()

            ActivityLog.log_activity(
                action_type='profile_picture_removed',
                user=request.user,
                description=f'{request.user.get_display_name()} removed profile picture for {user.get_display_name()}',
                request=request,
                object_type='user',
                object_id=user.user_id,
                object_repr=user.get_display_name()
            )

            messages.success(request, f'Profile picture removed for {user.get_display_name()}. User will be notified.')
        else:
            messages.info(request, f'{user.get_display_name()} does not have a profile picture.')

    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found')

    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_manage_users'))


@require_admin_v2_auth
def manage_login_history(request):
    """
    View and manage login history
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    suspicious_filter = request.GET.get('suspicious', '')
    user_search = request.GET.get('user', '')

    # Build query
    logins_list = LoginHistory.objects.select_related('user').order_by('-timestamp')

    if suspicious_filter == 'yes':
        logins_list = logins_list.filter(is_suspicious=True)

    if user_search:
        logins_list = logins_list.filter(
            Q(user__first_name__icontains=user_search) |
            Q(user__last_name__icontains=user_search) |
            Q(user__username__icontains=user_search)
        )

    # Paginate
    paginator = Paginator(logins_list, 50)
    page_number = request.GET.get('page')
    logins = paginator.get_page(page_number)

    context = {
        'logins': logins,
        'suspicious_filter': suspicious_filter,
        'user_search': user_search,
    }

    return render(request, 'admin_v2/manage_login_history.html', context)


@require_admin_v2_auth
def manage_announcements(request):
    """
    Manage all announcements
    """
    from django.core.paginator import Paginator

    # Get filter parameters
    active_filter = request.GET.get('active', '')

    # Build query
    announcements_list = Announcement.objects.select_related('posted_by').order_by('-posted_at')

    if active_filter == 'yes':
        announcements_list = announcements_list.filter(is_active=True)
    elif active_filter == 'no':
        announcements_list = announcements_list.filter(is_active=False)

    # Paginate
    paginator = Paginator(announcements_list, 25)
    page_number = request.GET.get('page')
    announcements = paginator.get_page(page_number)

    context = {
        'announcements': announcements,
        'active_filter': active_filter,
    }

    return render(request, 'admin_v2/manage_announcements.html', context)


@require_admin_v2_auth
def delete_announcement(request, announcement_id):
    """
    Delete an announcement
    """
    if request.method == 'POST':
        try:
            announcement = Announcement.objects.get(id=announcement_id)
            title = announcement.title
            announcement.delete()

            ActivityLog.log_activity(
                action_type='announcement_deleted',
                user=request.user,
                description=f'{request.user.get_display_name()} deleted announcement: {title}',
                request=request
            )

            messages.success(request, f'Announcement "{title}" has been deleted')
        except Announcement.DoesNotExist:
            messages.error(request, 'Announcement not found')

    return redirect('admin_v2_manage_announcements')


@require_admin_v2_auth
def user_login_security(request, user_id):
    """
    Detailed login security view for a specific user
    Shows login history, alerts, IP addresses, and security controls
    """
    user = get_object_or_404(ParliamentUser, user_id=user_id)

    # Get login history
    login_history = LoginHistory.objects.filter(user=user).order_by('-timestamp')[:50]

    # Get security alerts (limited to last 25)
    alerts = LoginAlert.objects.filter(user=user).order_by('-created_at')[:25]

    # Get unique IPs from login history
    unique_ips = set()
    ip_info = []
    for login in login_history:
        ip = login.ip_address
        if ip and ip not in unique_ips:
            unique_ips.add(ip)
            # Check if IP is whitelisted or blacklisted
            is_whitelisted = IPWhitelist.objects.filter(ip_address=ip, is_active=True).exists()
            is_blacklisted = IPBlacklist.objects.filter(ip_address=ip, is_active=True).exists()

            ip_info.append({
                'ip': ip,
                'location': login.location_display,
                'last_used': login.timestamp,
                'is_whitelisted': is_whitelisted,
                'is_blacklisted': is_blacklisted,
                'risk_level': login.risk_level,
            })

    # Statistics (query separately to avoid slicing issues)
    stats = {
        'total_logins': LoginHistory.objects.filter(user=user).count(),
        'failed_logins': LoginHistory.objects.filter(user=user, status='failed').count(),
        'suspicious_logins': LoginHistory.objects.filter(user=user, is_suspicious=True).count(),
        'active_alerts': LoginAlert.objects.filter(user=user, status='new').count(),
        'unique_ips': len(unique_ips),
        'unique_locations': len(set(login.location_display for login in login_history if login.city)),
    }

    # Check if there's a temporary password to display from session
    temp_password_data = request.session.pop('temp_password_display', None)

    context = {
        'target_user': user,
        'login_history': login_history,
        'alerts': alerts,
        'ip_info': ip_info,
        'stats': stats,
        'temp_password_data': temp_password_data,  # Will be None if not present
    }

    return render(request, 'admin_v2/user_login_security.html', context)


@require_admin_v2_auth
@require_POST
def force_password_reset(request, user_id):
    """
    Force a user to reset their password
    """
    from django.core.mail import send_mail

    user = get_object_or_404(ParliamentUser, user_id=user_id)
    reason = request.POST.get('reason', 'Security concern flagged by admin')
    password_type = request.POST.get('password_type', 'random')
    send_email = request.POST.get('send_email') == 'true'

    # Determine the new password
    if password_type == 'custom':
        temp_password = request.POST.get('custom_password', '').strip()
        if not temp_password:
            messages.error(request, 'Custom password cannot be empty')
            return redirect('admin_v2_user_login_security', user_id=user_id)
        if len(temp_password) < 8:
            messages.error(request, 'Custom password should be at least 8 characters')
            return redirect('admin_v2_user_login_security', user_id=user_id)
    else:
        # Generate a temporary random password
        temp_password = generate_random_password(length=16)

    # Set the new password
    user.set_password(temp_password)
    user.force_password_change = False  # Allow them to use this password
    user.save()

    # Log the action
    ActivityLog.log_activity(
        action_type='forced_password_reset',
        user=request.user,
        description=f'{request.user.get_display_name()} forced password reset for {user.get_display_name()}. Reason: {reason}',
        request=request,
        object_type='user',
        object_id=user.user_id,
        object_repr=user.get_display_name()
    )

    # Create a security alert for the user
    alert = LoginAlert.objects.create(
        user=user,
        alert_type='other',
        severity='high',
        status='resolved',
        title='Password Reset by Administrator',
        description=f'Your password was reset by an administrator. Reason: {reason}',
        reviewed_by=request.user,
        reviewed_at=timezone.now(),
        resolution_notes=f'New password: {temp_password}',
        user_notified=send_email
    )

    # Send email notification if requested
    email_sent = False
    if send_email and user.email:
        try:
            email_subject = 'Your Parliament Password Has Been Reset'
            email_body = f"""Hello {user.get_display_name()},

Your Parliament account password has been reset by an administrator.

Reason: {reason}

Your new password is: {temp_password}

Please log in using this password. For security reasons, you may want to change it after logging in.

If you did not request this password reset or have any concerns, please contact an administrator immediately.

Best regards,
Parliament Administration Team"""

            send_mail(
                email_subject,
                email_body,
                settings.DEFAULT_FROM_EMAIL,
                [user.email],
                fail_silently=False,
            )
            email_sent = True
        except Exception as e:
            messages.warning(
                request,
                f'Password was reset but email failed to send to {user.email}. Error: {str(e)}. '
                f'New password is stored in security alert resolution notes.'
            )

    # Store the password in request.session to show it only on the next admin panel page
    # This prevents it from showing on login screen if user gets logged out
    if email_sent:
        messages.success(
            request,
            f'Password reset for {user.get_display_name()}. Email sent to {user.email}. '
            f'The new password is also stored in the security alert below for your records.'
        )
    else:
        # Only show password in admin panel context, store it in session temporarily
        request.session['temp_password_display'] = {
            'user': user.get_display_name(),
            'password': temp_password,
            'email': user.email if user.email else None
        }
        if not user.email:
            messages.warning(
                request,
                f'Password reset for {user.get_display_name()}. No email on file - new password will be displayed on next page.'
            )
        elif not send_email:
            messages.info(
                request,
                f'Password reset for {user.get_display_name()}. Email not sent as requested - new password will be displayed on next page.'
            )

    return redirect('admin_v2_user_login_security', user_id=user_id)


@require_admin_v2_auth
@require_POST
def add_ip_to_whitelist(request):
    """
    Add an IP address to the whitelist
    """
    ip_address = request.POST.get('ip_address', '').strip()
    description = request.POST.get('description', '')

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Check if already whitelisted
    if IPWhitelist.objects.filter(ip_address=ip_address, is_active=True).exists():
        messages.warning(request, f'IP {ip_address} is already whitelisted')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Create whitelist entry
    IPWhitelist.objects.create(
        ip_address=ip_address,
        description=description or f'Added by {request.user.get_display_name()}',
        added_by=request.user
    )

    ActivityLog.log_activity(
        action_type='ip_whitelisted',
        user=request.user,
        description=f'{request.user.get_display_name()} added {ip_address} to whitelist: {description}',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been added to whitelist')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def add_ip_to_blacklist(request):
    """
    Add an IP address to the blacklist
    """
    ip_address = request.POST.get('ip_address', '').strip()
    reason = request.POST.get('reason', '')

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Check if already blacklisted
    if IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists():
        messages.warning(request, f'IP {ip_address} is already blacklisted')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Create blacklist entry
    IPBlacklist.objects.create(
        ip_address=ip_address,
        reason=reason or 'Suspicious activity',
        added_by=request.user
    )

    ActivityLog.log_activity(
        action_type='ip_blacklisted',
        user=request.user,
        description=f'{request.user.get_display_name()} blacklisted {ip_address}: {reason}',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been added to blacklist')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def remove_ip_from_whitelist(request):
    """
    Remove an IP address from the whitelist
    """
    ip_address = request.POST.get('ip_address', '').strip()

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Deactivate whitelist entry
    entries = IPWhitelist.objects.filter(ip_address=ip_address, is_active=True)
    count = entries.count()
    entries.update(is_active=False)

    ActivityLog.log_activity(
        action_type='ip_whitelist_removed',
        user=request.user,
        description=f'{request.user.get_display_name()} removed {ip_address} from whitelist',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been removed from whitelist ({count} entries deactivated)')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
@require_POST
def remove_ip_from_blacklist(request):
    """
    Remove an IP address from the blacklist
    """
    ip_address = request.POST.get('ip_address', '').strip()

    if not ip_address:
        messages.error(request, 'IP address is required')
        return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))

    # Deactivate blacklist entry
    entries = IPBlacklist.objects.filter(ip_address=ip_address, is_active=True)
    count = entries.count()
    entries.update(is_active=False)

    ActivityLog.log_activity(
        action_type='ip_blacklist_removed',
        user=request.user,
        description=f'{request.user.get_display_name()} removed {ip_address} from blacklist',
        request=request
    )

    messages.success(request, f'IP {ip_address} has been removed from blacklist ({count} entries deactivated)')
    return redirect(request.META.get('HTTP_REFERER', 'admin_v2_dashboard'))


@require_admin_v2_auth
def manage_ip_whitelist(request):
    """
    Manage IP whitelist entries
    """
    whitelist_entries = IPWhitelist.objects.filter(is_active=True).order_by('-added_at')

    context = {
        'whitelist_entries': whitelist_entries,
    }

    return render(request, 'admin_v2/ip_whitelist.html', context)


@require_admin_v2_auth
def manage_ip_blacklist(request):
    """
    Manage IP blacklist entries
    """
    blacklist_entries = IPBlacklist.objects.filter(is_active=True).order_by('-added_at')

    context = {
        'blacklist_entries': blacklist_entries,
    }

    return render(request, 'admin_v2/ip_blacklist.html', context)


@require_admin_v2_auth
def manage_security_alerts(request):
    """
    Manage security alerts across all users
    """
    # Filter parameters
    status_filter = request.GET.get('status', '')
    severity_filter = request.GET.get('severity', '')

    alerts = LoginAlert.objects.select_related('user', 'login_history').order_by('-created_at')

    if status_filter:
        alerts = alerts.filter(status=status_filter)
    if severity_filter:
        alerts = alerts.filter(severity=severity_filter)

    # Statistics
    stats = {
        'total_alerts': LoginAlert.objects.count(),
        'new_alerts': LoginAlert.objects.filter(status='new').count(),
        'investigating': LoginAlert.objects.filter(status='investigating').count(),
        'critical_alerts': LoginAlert.objects.filter(severity='critical', status='new').count(),
        'high_alerts': LoginAlert.objects.filter(severity='high', status='new').count(),
    }

    context = {
        'alerts': alerts[:100],  # Limit to 100 most recent
        'stats': stats,
        'status_filter': status_filter,
        'severity_filter': severity_filter,
    }

    return render(request, 'admin_v2/security_alerts.html', context)


@require_admin_v2_auth
def send_test_announcement_email(request):
    """
    Send a test announcement email to the current user.
    Uses the same template and formatting as real announcement emails.
    """
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags

    if request.method != 'POST':
        messages.error(request, 'Invalid request method')
        return redirect('admin_v2_dashboard')

    user = request.user

    # Check if user has an email set
    if not user.email:
        messages.error(request, 'You do not have an email address set. Please add one in your profile first.')
        return redirect('admin_v2_dashboard')

    # Create a mock announcement object for testing
    class MockAnnouncement:
        def __init__(self):
            self.id = 0
            self.title = "Test Announcement - Email System Check"
            self.content = """This is a TEST email from the Alpha Mu Parliament system.

If you are receiving this email, it means the announcement email system is working correctly!

This email was sent from the Admin-v2 dashboard to verify email delivery and formatting before the demo.

Test details:
• Email template: announcement_notification.html
• Tracking pixel: Included (pointing to test endpoint)
• HTML formatting: Enabled
• Plain text fallback: Included

-- This is an automated test message --"""
            self.posted_at = timezone.now()
            self.posted_by = user
            self.event_date = None  # No event date for test

    mock_announcement = MockAnnouncement()

    # Get site URL
    site_url = getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')

    # Generate tracking URL (will be a test/invalid one)
    tracking_url = f"{site_url}/track/announcement/0/user/{user.user_id}/"

    try:
        # Create HTML email with tracking pixel
        html_message = render_to_string('emails/announcement_notification.html', {
            'announcement': mock_announcement,
            'site_url': site_url,
            'tracking_url': tracking_url,
            'user': user,
        })

        # Create plain text version
        plain_message = strip_tags(html_message)

        # Send the email
        msg = EmailMultiAlternatives(
            subject="[TEST] New Announcement: Test Announcement - Email System Check",
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email]
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send()

        # Log the activity
        ActivityLog.objects.create(
            user=user,
            action_category='settings',
            action_type='settings_changed',
            description=f'Sent test announcement email to {user.email}',
            ip_address=get_client_ip(request)
        )

        messages.success(request, f'Test email sent successfully to {user.email}! Check your inbox (and spam folder).')

    except Exception as e:
        messages.error(request, f'Failed to send test email: {str(e)}')

    return redirect('admin_v2_dashboard')


@require_admin_v2_auth
def preview_test_email(request):
    """
    Render the test announcement email in the browser for preview.
    This allows testing the tracking pixel and viewing the email design.
    """
    from django.template.loader import render_to_string
    from django.http import HttpResponse

    user = request.user

    # Create a mock announcement object for testing
    class MockAnnouncement:
        def __init__(self):
            self.id = 0
            self.title = "Test Announcement - Email System Check"
            self.content = """This is a TEST email from the Alpha Mu Parliament system.

If you are receiving this email, it means the announcement email system is working correctly!

This email was sent from the Admin-v2 dashboard to verify email delivery and formatting before the demo.

Test details:
• Email template: announcement_notification.html
• Tracking pixel: Included (pointing to test endpoint)
• HTML formatting: Enabled
• Plain text fallback: Included

-- This is an automated test message --"""
            self.posted_at = timezone.now()
            self.posted_by = user
            self.event_date = None  # No event date for test

    mock_announcement = MockAnnouncement()

    # Get site URL
    site_url = getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')

    # Generate tracking URL (will be a test/invalid one)
    tracking_url = f"{site_url}/track/announcement/0/user/{user.user_id}/"

    # Render the email HTML
    html_content = render_to_string('emails/announcement_notification.html', {
        'announcement': mock_announcement,
        'site_url': site_url,
        'tracking_url': tracking_url,
        'user': user,
    })

    # Log the preview action
    ActivityLog.objects.create(
        user=user,
        action_category='settings',
        action_type='view',
        description='Previewed test announcement email in browser',
        ip_address=get_client_ip(request)
    )

    return HttpResponse(html_content)
