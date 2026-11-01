from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from src.committee_management import ROLE_FIELDS, CommitteeActionError, add_role, can_manage_roles
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
    if not can_manage_roles(request.user, committee):
        messages.error(request, 'You do not have permission to manage this committee.')
        return redirect('committee_home', code=code)

    user_id = request.POST.get('user_id')
    role_type = request.POST.get('role_type')

    try:
        user = ParliamentUser.objects.get(pk=user_id)

        # Pledges cannot be added to committees (src/committee_management).
        # An unknown role_type is ignored, as it always was.
        if role_type in ROLE_FIELDS:
            messages.success(request, add_role(committee, user, role_type))
    except CommitteeActionError as exc:
        messages.error(request, exc.message)
    except ParliamentUser.DoesNotExist:
        messages.error(request, 'User not found.')

    return redirect('committee_home', code=code)