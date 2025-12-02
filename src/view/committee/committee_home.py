from django.shortcuts import render, get_object_or_404
from src.models import Committee, CommitteePermissions

def committee_home(request, id):
    committee = get_object_or_404(Committee, id=id)
    perm = CommitteePermissions.objects.filter(
        committee=committee, user=request.user
    ).first()

    return render(request, "committee/home.html", {
        "committee": committee,
        "perm": perm
    })