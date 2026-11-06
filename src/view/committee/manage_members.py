from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from src.models import Committee, CommitteePermissions, ParliamentUser
from src.committee_management import MANAGE_ACTIONS, CommitteeActionError, manage_members_action
from src.view.committee.member_permissions import can_manage_members


@login_required
def committee_manage_members(request, code):
    committee = get_object_or_404(Committee, code=code)
    perm = CommitteePermissions.objects.filter(
        user=request.user,
        committee=committee
    ).first()

    # Chairs (incl. exec board), the VP, site admins, and members a chair has
    # let manage members (Member Permissions page, 10-29-26). This was behind
    # @committee_chair_required, so the can_manage_members check below it
    # could never let anyone else in.
    if not can_manage_members(request.user, committee):
        return HttpResponseForbidden("You cannot manage committee members.")

    # Exclude pledges from available users for committee membership
    all_users = ParliamentUser.active.exclude(member_type='Pledge')

    if request.method == "POST":
        action = request.POST.get("action")
        user_id = request.POST.get("user_id")
        # 11-06-26: an unknown id is a 404, not a 500.
        target = get_object_or_404(ParliamentUser, user_id=user_id)

        # Pledges can't be added; an unknown action does nothing
        # (src/committee_management, shared with the app).
        if action in MANAGE_ACTIONS:
            try:
                messages.success(request, manage_members_action(committee, target, action))
            except CommitteeActionError as exc:
                messages.error(request, exc.message)

        # 11-06-26: the URL is named 'manage_members'; 'committee_manage_members'
        # raised NoReverseMatch (a 500) after every add or remove, once saved.
        return redirect("manage_members", code=code)

    return render(request, "committee/manage_members.html", {
        "committee": committee,
        "perm": perm,
        "all_users": all_users,
    })