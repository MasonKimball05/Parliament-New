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
from src.models_feature_flags import FeatureFlag, PageToggle
from src.models import (
    ParliamentUser, Legislation, Event, Committee,
    Announcement, ActivityLog, LoginHistory, LoginAlert
)
import os


ALLOWED_USER_ID = '73'  # Your user ID


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
        document_count=Count('committeedocument')
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
    users_list = ParliamentUser.objects.order_by('last_name', 'first_name')

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
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
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
