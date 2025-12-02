from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from src.models import Committee, CommitteePermissions

def committee_vote(request, id):
    committee = get_object_or_404(Committee, id=id)
    perm = CommitteePermissions.objects.filter(
        user=request.user, committee=committee
    ).first()

    if not perm or not perm.can_vote:
        return HttpResponseForbidden("You cannot vote in this committee.")

    # TODO: Build committee-specific voting model
    votes = []

    return render(request, "committee/vote.html", {
        "committee": committee,
        "votes": votes,
        "perm": perm
    })