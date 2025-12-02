from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from src.models import Committee, CommitteePermissions

def committee_documents(request, id):
    committee = get_object_or_404(Committee, id=id)
    perm = CommitteePermissions.objects.filter(
        user=request.user, committee=committee
    ).first()

    if not perm or not perm.can_view_docs:
        return HttpResponseForbidden("You do not have access to committee documents.")

    # TODO: Hook committee docs into a model later
    documents = []

    return render(request, "committee/documents.html", {
        "committee": committee,
        "documents": documents,
        "perm": perm,
    })