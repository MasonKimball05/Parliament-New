import logging
from functools import wraps
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from src.models import Committee
from src.constants import MemberType
# v3.19.6 — the predicates behind `officer_required` and `vpp_required`.
# They live in src/permissions.py because the file views in
# src/view/serve_private_upload.py need the same rule WITHOUT a request,
# and because everything defined in THIS module must call `_gate`
# (test_every_authz_decorator_routes_through_the_gate_helper) — which a
# pure predicate cannot honestly do. See that module's docstring.
from src.permissions import user_is_officer_or_chair, user_is_vpp
from src.dev_mode import record_permission

# Set up the logger to capture function call logs
logger = logging.getLogger('function_calls')


def _gate(name, allowed, detail=''):
    """
    Record a permission decision for the dev-mode Perms panel and return it.

    Every authorization decorator in this module routes its decision through
    here. Before 07-28-26 only `_get_kai_access` was instrumented, so the panel
    read "no permission gate ran" on pages that were in fact gated — officer
    pages being the obvious case. A gate that isn't recorded here is invisible
    to dev mode, so add new ones to this pattern.

    `record_permission` is a no-op when dev mode is off (one ContextVar lookup)
    and is exception-proof, so this is safe on every request for every user.
    """
    record_permission(name, 'allowed' if allowed else 'DENIED', detail)
    return allowed


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
        is_admin = request.user.is_admin
        allowed = is_admin or committee.is_chair(request.user)
        if not _gate('committee_chair_required', allowed,
                     f'committee={code}, ' + ('via is_admin' if is_admin else 'chair check')):
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
        allowed = user_is_officer_or_chair(request.user)
        if not _gate('officer_required', allowed,
                     f'member_type={request.user.member_type}, is_officer={request.user.is_officer}'):
            return HttpResponseForbidden("Officers and chairs only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def officer_or_advisor_required(view_func):
    """Restrict access to officers, chairs, and advisors (read-only for advisors)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not _gate('officer_or_advisor_required', request.user.can_view_officer_pages,
                     f'member_type={request.user.member_type}'):
            return HttpResponseForbidden("Officers and advisors only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_required(view_func):
    """Restrict access to admins only."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not _gate('admin_required', request.user.is_admin, 'is_admin field'):
            return HttpResponseForbidden("Admins only.")
        return view_func(request, *args, **kwargs)
    return wrapper


def exclude_pledges(view_func):
    """Block pledges from accessing a view."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not _gate('exclude_pledges', not request.user.is_pledge,
                     f'is_pledge={request.user.is_pledge}'):
            return render(request, 'errors/pledge_restricted.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


def kai_chair_required(view_func):
    """Restrict access to Kai committee chairs and admins."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        try:
            kai_committee = Committee.objects.get(is_kai_committee=True)
            allowed = kai_committee.is_chair(request.user) or request.user.is_admin
            if not _gate('kai_chair_required', allowed,
                         'via is_admin' if request.user.is_admin else 'kai chair check'):
                messages.error(request, 'Only Kai chairs can access this page.')
                return redirect('home')
        except Committee.DoesNotExist:
            if not _gate('kai_chair_required', request.user.is_admin,
                         'no Kai committee exists — admin-only fallback'):
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
                if not _gate(f'pledge_page_allowed({url_name})',
                             PledgePageRestriction.is_allowed(url_name, phase),
                             f'pledge, phase={phase}'):
                    return render(request, 'errors/pledge_restricted.html', status=403)
            else:
                _gate(f'pledge_page_allowed({url_name})', True, 'not a pledge — passthrough')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def cnb_required(view_func):
    """Restrict access to admins and users with the CNB (Constitution & Bylaws Chair) role."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not _gate('cnb_required', request.user.has_cnb_permission, 'has_cnb_permission'):
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
        # Hardcoded single user id is intentional — see CLAUDE.md.
        if not _gate('bug_admin_required', str(request.user.user_id) == '73',
                     'hardcoded user_id 73 (intentional)'):
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
            _gate('vpp_required', True, 'via is_admin')
            return view_func(request, *args, **kwargs)

        if request.user.roles.filter(code__iexact='VPP').exists():
            _gate('vpp_required', True, 'has VPP role')
            return view_func(request, *args, **kwargs)

        _gate('vpp_required', False, 'not admin, no VPP role')

        # NOTE: no DEBUG bypass — authorization must not depend on DEBUG (a stray
        # DJANGO_DEBUG=True in prod would promote every member to VPP). For local
        # testing, grant the dev user the VPP role. (07-22 auth security sweep, C.)
        messages.error(request, 'Only the Vice President of Programming can access this page.')
        return redirect('home')

    return wrapper
