import logging
from functools import wraps
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from src.models import Committee
from src.constants import MemberType

# Set up the logger to capture function call logs
logger = logging.getLogger('function_calls')


def log_function_call(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        function_name = func.__name__
        action = kwargs.get('action', 'No specific action')
        logger.info(f"User {request.user.username} called {function_name} with arguments: {args}, {kwargs}, Action: {action}")
        return func(request, *args, **kwargs)
    return wrapper


def committee_chair_required(view_func):
    @wraps(view_func)
    def wrapper(request, code, *args, **kwargs):
        committee = get_object_or_404(Committee, code=code)

        # Allow admins to bypass chair requirement
        if not request.user.is_admin and not committee.is_chair(request.user):
            return HttpResponseForbidden("Chairs only.")

        return view_func(request, code, *args, **kwargs)
    return wrapper


def officer_required(view_func):
    """Restrict access to officers, chairs, and admins (excludes advisors and pledges)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        # Allow Officers, Chairs, and Admins
        if not (request.user.is_officer or request.user.member_type == MemberType.CHAIR):
            return HttpResponseForbidden("Officers and chairs only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def officer_or_advisor_required(view_func):
    """Restrict access to officers, chairs, and advisors (read-only for advisors)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not request.user.can_view_officer_pages:
            return HttpResponseForbidden("Officers and advisors only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_required(view_func):
    """Restrict access to admins only."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not request.user.is_admin:
            return HttpResponseForbidden("Admins only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def exclude_pledges(view_func):
    """Block pledges from accessing a view."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if request.user.is_pledge:
            return render(request, 'errors/pledge_restricted.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def kai_chair_required(view_func):
    """Restrict access to Kai committee chairs and admins."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            kai_committee = Committee.objects.get(is_kai_committee=True)
            if not kai_committee.is_chair(request.user) and not request.user.is_admin:
                messages.error(request, 'Only Kai chairs can access this page.')
                return redirect('home')
        except Committee.DoesNotExist:
            if not request.user.is_admin:
                messages.error(request, 'Kai committee not found. Please contact an administrator.')
                return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


def pledge_page_allowed(url_name):
    """
    Decorator factory: blocks pledges from the decorated view unless the VPE has
    explicitly allowed it for their current phase via PledgePageRestriction.

    Non-pledge users always pass through.

    Usage:
        @login_required
        @pledge_page_allowed('directory')
        def directory(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.is_pledge:
                from src.models.education import PledgePageRestriction
                phase = getattr(request.user, 'pledge_phase', None) or 'all'
                if not PledgePageRestriction.is_allowed(url_name, phase):
                    return render(request, 'errors/pledge_restricted.html', status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def cnb_required(view_func):
    """Restrict access to admins and users with the CNB (Constitution & Bylaws Chair) role."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not request.user.has_cnb_permission:
            messages.error(request, 'Constitution & Bylaws Chair access required.')
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


def bug_admin_required(view_func):
    """Restrict access to the designated bug-tracker admin (user_id 73)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if str(request.user.user_id) != '73':
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('bug_tracker')
        return view_func(request, *args, **kwargs)
    return wrapper


def vpp_required(view_func):
    """
    Restrict access to VPP (Vice President of Programming) role holders and admins.
    Used for Service Hours officer pages.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if request.user.is_admin:
            return view_func(request, *args, **kwargs)

        if request.user.roles.filter(code__iexact='VPP').exists():
            return view_func(request, *args, **kwargs)

        # NOTE: no DEBUG bypass — authorization must not depend on DEBUG (a stray
        # DJANGO_DEBUG=True in prod would promote every member to VPP). For local
        # testing, grant the dev user the VPP role. (07-22 auth security sweep, C.)
        messages.error(request, 'Only the Vice President of Programming can access this page.')
        return redirect('home')

    return wrapper
