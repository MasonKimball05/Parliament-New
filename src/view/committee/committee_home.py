from django.shortcuts import render, get_object_or_404
from src.models import Committee, CommitteePermissions

def committee_home(request, id):
    committee = get_object_or_404(Committee.objects.select_related('role'), id=id)
    perm = CommitteePermissions.objects.filter(
        committee=committee, user=request.user
    ).first()
    
    # Get committee VP
    committee_vp = committee.get_vp()

    return render(request, "committee/home.html", {
        "committee": committee,
        "perm": perm,
        "committee_vp": committee_vp
    })
