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
    # Gather site statistics
    stats = {
        'users': {
            'total': ParliamentUser.objects.count(),
            'active': ParliamentUser.objects.filter(member_status='Active').count(),
            'officers': ParliamentUser.objects.filter(member_type='Officer').count(),
            'members': ParliamentUser.objects.filter(member_type='Member').count(),
            'pledges': ParliamentUser.objects.filter(member_type='Pledge').count(),
        },
        'legislation': {
            'total': Legislation.objects.count(),
            'draft': Legislation.objects.filter(status='draft').count(),
            'passed': Legislation.objects.filter(status='passed').count(),
            'removed': Legislation.objects.filter(status='removed').count(),
        },
        'events': {
            'total': Event.objects.count(),
            'upcoming': Event.objects.filter(date_time__gte=timezone.now()).count(),
            'past': Event.objects.filter(date_time__lt=timezone.now()).count(),
        },
        'committees': {
            'total': Committee.objects.count(),
            'active': Committee.objects.filter(is_active=True).count(),
        },
        'announcements': {
            'total': Announcement.objects.count(),
            'active': Announcement.objects.filter(is_active=True).count(),
        },
        'security': {
            'total_logins': LoginHistory.objects.count(),
            'recent_alerts': LoginAlert.objects.filter(status='new').count(),
            'recent_activities': ActivityLog.objects.count(),
        }
    }

    # Get feature flags grouped by category
    feature_flags = {}
    for category, category_name in FeatureFlag.CATEGORY_CHOICES:
        flags = FeatureFlag.objects.filter(category=category)
        if flags.exists():
            feature_flags[category_name] = flags

    # Get page toggles
    page_toggles = PageToggle.objects.all()

    # Recent activity logs (last 20)
    recent_logs = ActivityLog.objects.select_related('user').order_by('-timestamp')[:20]

    context = {
        'stats': stats,
        'feature_flags': feature_flags,
        'page_toggles': page_toggles,
        'recent_logs': recent_logs,
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
