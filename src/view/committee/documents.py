from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseForbidden
from src.models import Committee, CommitteePermissions, CommitteeDocument
from django.contrib.auth.decorators import login_required
from src.feature_flag_decorators import require_page_enabled

@login_required
@require_page_enabled('committee_documents')
def committee_documents(request, code):  # Changed from id to code
    committee = get_object_or_404(Committee, code=code)  # Changed to use code
    user = request.user

    perm = CommitteePermissions.objects.filter(
        user=user, committee=committee
    ).first()

    if not perm or not perm.can_view_docs:
        return HttpResponseForbidden("You cannot view documents in this committee.")

    # Get all documents for this committee
    all_documents = CommitteeDocument.objects.filter(committee=committee)

    # Filter documents based on visibility permissions
    documents = [doc for doc in all_documents if doc.can_user_view(user)]

    # Check if user is VP (committee admin) or chair
    is_vp = committee.is_vp(user)
    is_chair = committee.is_chair(user)
    can_delete = is_vp or is_chair

    return render(request, "committee/documents.html", {
        "committee": committee,
        "documents": documents,
        "perm": perm,
        "can_delete": can_delete,
        "is_vp": is_vp
    })