"""
Slating System Permission Decorators

These decorators control access to slating views based on user roles:
- slating_admin_required: Admins only
- slating_chair_required: Slating committee chair or admin
- slating_committee_required: Slating committee members (read access)
- voting_member_required: Active members who can vote (excludes pledges)
"""

from functools import wraps
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponseForbidden
from src.models import SlatingPeriod


def slating_admin_required(view_func):
    """
    Decorator to restrict access to admins only.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not request.user.is_admin:
            messages.error(request, 'Only administrators can access this page.')
            return redirect('slating_dashboard')

        return view_func(request, *args, **kwargs)

    return wrapper


def slating_chair_required(view_func):
    """
    Decorator to check if user is a slating committee chair or admin.
    Expects period_id in URL kwargs.
    """
    @wraps(view_func)
    def wrapper(request, period_id=None, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if request.user.is_admin:
            return view_func(request, period_id, *args, **kwargs)

        if period_id:
            period = get_object_or_404(SlatingPeriod, id=period_id)
            if period.slating_committee and period.slating_committee.is_chair(request.user):
                return view_func(request, period_id, *args, **kwargs)

        messages.error(request, 'Only slating committee chairs can access this page.')
        return redirect('slating_dashboard')

    return wrapper


def slating_committee_required(view_func):
    """
    Decorator for slating committee members (read access to applications).
    """
    @wraps(view_func)
    def wrapper(request, period_id=None, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if request.user.is_admin:
            return view_func(request, period_id, *args, **kwargs)

        if period_id:
            period = get_object_or_404(SlatingPeriod, id=period_id)
            if period.slating_committee:
                committee = period.slating_committee
                if committee.is_member(request.user) or committee.is_chair(request.user):
                    return view_func(request, period_id, *args, **kwargs)

        messages.error(request, 'Only slating committee members can access this page.')
        return redirect('slating_dashboard')

    return wrapper


def voting_member_required(view_func):
    """
    Decorator for chapter voting (excludes pledges, inactive members).
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if not request.user.can_vote:
            messages.error(request, 'You are not eligible to vote.')
            return redirect('slating_dashboard')

        return view_func(request, *args, **kwargs)

    return wrapper


def can_manage_period(user, period):
    """
    Helper function to check if a user can manage a slating period.
    Returns True if user is admin or slating committee chair.
    """
    if user.is_admin:
        return True

    if period.slating_committee:
        return period.slating_committee.is_chair(user)

    return False


def can_view_applications(user, period):
    """
    Helper function to check if a user can view applications for a period.
    Returns True if user is admin or slating committee member.
    """
    if user.is_admin:
        return True

    if period.slating_committee:
        committee = period.slating_committee
        return committee.is_member(user) or committee.is_chair(user)

    return False
