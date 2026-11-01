from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from src.committee_management import ROLE_FIELDS, can_manage_roles, remove_role
from src.models import Committee, ParliamentUser

@login_required
@require_POST
def committee_remove_member(request, code):
    """Remove a member from a committee role"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions. v3.26.6 — see add_member.py's comment; same widening
    # (VP/admin/site-admin -> also chairs, including role_type='chair'),
    # `committee.is_chair()` for the same consistency reason.
    if not can_manage_roles(request.user, committee):
        messages.error(request, 'You do not have permission to manage this committee.')
        return redirect('committee_home', code=code)

    user_id = request.POST.get('user_id')
    role_type = request.POST.get('role_type')

    try:
        user = ParliamentUser.objects.get(pk=user_id)

        # Removing a member also drops their vote (src/committee_management).
        if role_type in ROLE_FIELDS:
            messages.success(request, remove_role(committee, user, role_type))
    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found.')

    return redirect('committee_home', code=code)