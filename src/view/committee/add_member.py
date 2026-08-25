from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from src.models import Committee, ParliamentUser

@login_required
@require_POST
def committee_add_member(request, code):
    """Add a member to a committee role"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions.
    #
    # v3.26.6 — widened from VP/admin-only to also allow chairs, per Mason's
    # request, including for role_type='chair' (a chair may now add another
    # chair, not just members/advisors/voters). `committee_home.html` was
    # already showing this exact "+ Add" control to chairs (`can_manage`
    # includes `is_chair` — see committee_home.py) and silently rejecting
    # the submit for every role type, not just chair — this closes that gap
    # too, not only the one that was asked for.
    #
    # `committee.is_chair()` (not a raw `.chairs.filter()` check) to match
    # `committee_chair_required` and stay consistent with the rest of the
    # codebase's chair check — it also covers exec-board members, same as
    # everywhere else "chair-level" is decided.
    if not (committee.is_chair(request.user) or committee.is_vp(request.user) or request.user.is_admin):
        messages.error(request, 'You do not have permission to manage this committee.')
        return redirect('committee_home', code=code)

    user_id = request.POST.get('user_id')
    role_type = request.POST.get('role_type')

    try:
        user = ParliamentUser.objects.get(pk=user_id)

        # Pledges cannot be added to committees
        if user.is_pledge:
            messages.error(request, 'Pledges cannot be added to committees.')
            return redirect('committee_home', code=code)

        if role_type == 'member':
            committee.members.add(user)
            messages.success(request, f'{user.name} has been added as a member.')
        elif role_type == 'chair':
            committee.chairs.add(user)
            messages.success(request, f'{user.name} has been added as a chair.')
        elif role_type == 'advisor':
            committee.advisors.add(user)
            messages.success(request, f'{user.name} has been added as an advisor.')
        elif role_type == 'voter':
            committee.voting_members.add(user)
            messages.success(request, f'{user.name} has been added as a voting member.')
    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found.')

    return redirect('committee_home', code=code)