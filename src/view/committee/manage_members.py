from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from src.models import Committee, CommitteePermissions, ParliamentUser
from src.decorators import *

@committee_chair_required
@login_required
def committee_manage_members(request, code):
    committee = get_object_or_404(Committee, code=code)
    perm = CommitteePermissions.objects.filter(
        user=request.user,
        committee=committee
    ).first()

    # Allow site admins, committee admin, or committee chairs to bypass permission checks
    is_committee_admin = committee.admin == request.user if committee.admin else False
    is_chair = committee.chairs.filter(pk=request.user.pk).exists()
    if not request.user.is_admin and not is_committee_admin and not is_chair:
        if not perm or not perm.can_manage_members:
            return HttpResponseForbidden("You cannot manage committee members.")

    all_users = ParliamentUser.active.all()

    if request.method == "POST":
        action = request.POST.get("action")
        user_id = request.POST.get("user_id")
        target = ParliamentUser.objects.get(user_id=user_id)

        if action == "add_member":
            committee.members.add(target)
        elif action == "remove_member":
            committee.members.remove(target)
        elif action == "add_advisor":
            committee.advisors.add(target)
        elif action == "remove_advisor":
            committee.advisors.remove(target)
        elif action == "add_voter":
            committee.voting_members.add(target)
        elif action == "remove_voter":
            committee.voting_members.remove(target)

        return redirect("committee_manage_members", code=code)

    return render(request, "committee/manage_members.html", {
        "committee": committee,
        "perm": perm,
        "all_users": all_users,
    })