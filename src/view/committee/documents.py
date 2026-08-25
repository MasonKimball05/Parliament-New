from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseForbidden
from src.models import Committee, CommitteePermissions, CommitteeDocument, ChapterMinutes
from django.contrib.auth.decorators import login_required
from src.feature_flag_decorators import require_page_enabled, check_feature_enabled
from src.view.committee.committee_minutes_editor import can_edit_committee_minutes

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

    # Build a map of document_id -> linked minutes for "Edit Minutes" links
    doc_ids = [doc.id for doc in documents]
    linked_minutes_qs = ChapterMinutes.objects.filter(
        published_document_id__in=doc_ids,
        committee=committee,
    ).values('id', 'published_document_id')
    linked_minutes_map = {row['published_document_id']: row['id'] for row in linked_minutes_qs}

    # Attach linked minutes id to each document object for easy template access
    for doc in documents:
        doc.linked_minutes_id = linked_minutes_map.get(doc.id)

    # Check if user is VP (committee admin) or chair
    is_vp = committee.is_vp(user)
    is_chair = committee.is_chair(user)
    can_delete = is_vp or is_chair
    can_edit_minutes = can_edit_committee_minutes(user, committee)

    # Version history is a real query per document (`document.versions`
    # ordered by -version_number per DocumentVersion.Meta), so it's only run
    # when the feature is actually on — a chapter that never enables
    # document_versioning pays nothing extra for this page.
    versioning_enabled = check_feature_enabled('document_versioning')
    if versioning_enabled:
        for doc in documents:
            doc.version_history = list(doc.versions.all())

    return render(request, "committee/documents.html", {
        "committee": committee,
        "documents": documents,
        "perm": perm,
        "can_delete": can_delete,
        "is_vp": is_vp,
        "is_chair": is_chair,
        "can_edit_minutes": can_edit_minutes,
        "versioning_enabled": versioning_enabled,
    })