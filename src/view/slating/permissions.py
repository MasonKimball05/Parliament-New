"""
Slating System Permission Decorators

These decorators control access to slating views based on user roles:
- slating_admin_required: Site admins only (dashboard, period creation)
- slating_chair_required: Period manager/committee chair — full setup access
- slating_committee_required: Committee members — read access to applications/interviews
- voting_member_required: Active members who can vote (excludes pledges)

Confidentiality model:
Once a slating period has a committee admin or slating_manager assigned, the period
enters "locked" mode. In locked mode, site admin status does NOT grant access —
only explicitly authorized roles (committee admin, committee chair, slating_manager,
committee members for read access) are permitted. This protects applicant and
deliberation confidentiality even from uninvolved site admins.

If no committee admin and no slating_manager are set, site admins retain access
so periods can always be managed during initial setup.
"""

from functools import wraps
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from src.models import SlatingPeriod


def _period_is_locked(period):
    """
    Returns True if the period has been explicitly assigned a responsible party,
    triggering confidential access control (no site-admin bypass).
    """
    return bool(
        period.slating_manager_id or
        (period.slating_committee_id and period.slating_committee.admin_id)
    )


def _user_can_manage(user, period):
    """
    Returns True if the user has chair-level management access to the period.
    Does NOT check is_admin — callers decide whether admin bypass applies.
    """
    if period.slating_manager_id and period.slating_manager_id == user.pk:
        return True
    if period.slating_committee:
        committee = period.slating_committee
        if committee.admin_id == user.pk:
            return True
        if committee.is_chair(user):
            return True
    return False


def _user_can_view(user, period):
    """
    Returns True if the user has committee member read access to the period.
    Does NOT check is_admin — callers decide whether admin bypass applies.
    """
    if _user_can_manage(user, period):
        return True
    if period.slating_committee:
        committee = period.slating_committee
        if committee.is_member(user) or committee.admin_id == user.pk:
            return True
    return False


def slating_admin_required(view_func):
    """
    Site admins only. Used for period creation and the slating dashboard.
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
    Full setup access: slating_manager, committee admin, or committee chair.

    If the period is locked (committee admin or slating_manager set), site admin
    status alone is NOT sufficient — the user must be one of the above roles.
    If the period is not yet locked, site admins retain access.
    """
    @wraps(view_func)
    def wrapper(request, period_id=None, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if period_id:
            period = get_object_or_404(SlatingPeriod, id=period_id)

            if _user_can_manage(request.user, period):
                return view_func(request, period_id, *args, **kwargs)

            # Site admin fallback only when period is not yet locked down
            if request.user.is_admin and not _period_is_locked(period):
                return view_func(request, period_id, *args, **kwargs)
        else:
            # No period context — allow site admins (e.g. dashboard actions)
            if request.user.is_admin:
                return view_func(request, period_id, *args, **kwargs)

        messages.error(request, 'You do not have access to manage this slating period.')
        return redirect('slating_dashboard')

    return wrapper


def slating_committee_required(view_func):
    """
    Read access for committee members (applications, interview notes).

    Same confidentiality model: if locked, site admin alone is not enough.
    """
    @wraps(view_func)
    def wrapper(request, period_id=None, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')

        if period_id:
            period = get_object_or_404(SlatingPeriod, id=period_id)

            if _user_can_view(request.user, period):
                return view_func(request, period_id, *args, **kwargs)

            # Site admin fallback only when period is not yet locked down
            if request.user.is_admin and not _period_is_locked(period):
                return view_func(request, period_id, *args, **kwargs)
        else:
            if request.user.is_admin:
                return view_func(request, period_id, *args, **kwargs)

        messages.error(request, 'You do not have access to view this slating period.')
        return redirect('slating_dashboard')

    return wrapper


def voting_member_required(view_func):
    """
    Chapter voting access — excludes pledges and ineligible members.
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
    Helper: True if user has chair-level access to the period.
    Respects the same locked/unlocked logic as slating_chair_required.
    """
    if _user_can_manage(user, period):
        return True
    if user.is_admin and not _period_is_locked(period):
        return True
    return False


def can_view_applications(user, period):
    """
    Helper: True if user can read applications and interview notes.
    Respects the same locked/unlocked logic as slating_committee_required.
    """
    if _user_can_view(user, period):
        return True
    if user.is_admin and not _period_is_locked(period):
        return True
    return False
