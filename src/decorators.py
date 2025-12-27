import logging
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from src.models import *

# Set up the logger to capture function call logs
logger = logging.getLogger('function_calls')

def log_function_call(func):
    def wrapper(request, *args, **kwargs):
        user = request.user  # Get the logged-in user
        function_name = func.__name__  # Get the function name
        action = kwargs.get('action', 'No specific action')  # Optional: You can specify an action in kwargs for more clarity

        # Log the function call details
        logger.info(f"User {user.username} called {function_name} with arguments: {args}, {kwargs}, Action: {action}")

        return func(request, *args, **kwargs)
    return wrapper

def committee_chair_required(view_func):
    def wrapper(request, id, *args, **kwargs):
        committee = get_object_or_404(Committee, id=id)

        if not committee.is_chair(request.user):
            return HttpResponseForbidden("Chairs only.")

        return view_func(request, id, *args, **kwargs)

    return wrapper

def officer_required(view_func):
    """Decorator to restrict access to officers and chairs (for write operations, excludes advisors and pledges)"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect('login')

        if not request.user.is_officer:
            return HttpResponseForbidden("Officers and chairs only.")
        return view_func(request, *args, **kwargs)
    return wrapper

def officer_or_advisor_required(view_func):
    """Decorator to restrict access to officers and advisors (read-only for advisors)"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect('login')

        if not request.user.can_view_officer_pages:
            return HttpResponseForbidden("Officers and advisors only.")
        return view_func(request, *args, **kwargs)
    return wrapper

def admin_required(view_func):
    """Decorator to restrict access to admins only"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect('login')

        if not request.user.is_admin:
            return HttpResponseForbidden("Admins only.")
        return view_func(request, *args, **kwargs)
    return wrapper

def exclude_pledges(view_func):
    """Decorator to exclude pledges from accessing a view"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect('login')

        if request.user.is_pledge:
            return HttpResponseForbidden("This page is not accessible to pledge members.")
        return view_func(request, *args, **kwargs)
    return wrapper