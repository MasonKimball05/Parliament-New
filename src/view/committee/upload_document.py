from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from src.models import Committee, CommitteePermissions

def committee_upload_document(request, id):
    committee = get_object_or_404(Committee, id=id)
    perm = CommitteePermissions.objects.filter(user=request.user, committee=committee).first()

    if not perm or not perm.can_upload_docs:
        return HttpResponseForbidden("You cannot upload documents.")

    if request.method == "POST":
        # TODO: Handle file upload later
        return redirect("committee_documents", id=id)

    return render(request, "committee/upload.html", {
        "committee": committee,
        "perm": perm,
    })