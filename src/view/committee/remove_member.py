from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from src.models import Committee, ParliamentUser

@login_required
@require_POST
def committee_remove_member(request, code):
    """Remove a member from a committee role"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not (committee.is_vp(request.user) or request.user.is_admin):
        messages.error(request, 'You do not have permission to manage this committee.')
        return redirect('committee_detail', code=code)

    user_id = request.POST.get('user_id')
    role_type = request.POST.get('role_type')

    try:
        user = ParliamentUser.objects.get(pk=user_id)

        if role_type == 'member':
            committee.members.remove(user)
            messages.success(request, f'{user.name} has been removed from members.')
        elif role_type == 'chair':
            committee.chairs.remove(user)
            messages.success(request, f'{user.name} has been removed from chairs.')
        elif role_type == 'advisor':
            committee.advisors.remove(user)
            messages.success(request, f'{user.name} has been removed from advisors.')
        elif role_type == 'voter':
            committee.voting_members.remove(user)
            messages.success(request, f'{user.name} has been removed from voting members.')
    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found.')

    return redirect('committee_detail', code=code)