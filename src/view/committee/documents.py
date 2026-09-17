from django.shortcuts import render, get_object_or_404
from django.http import HttpResponseForbidden
from src.models import Committee, CommitteePermissions, CommitteeDocument, ChapterMinutes
from src.models.documents import DocumentVersion
from django.contrib.auth.decorators import login_required
from src.feature_flag_decorators import require_page_enabled, check_feature_enabled
from src.view.committee.committee_minutes_editor import (
    can_edit_committee_minutes, can_edit_specific_minutes, is_committee_member_or_above,
)

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
    # select_related('uploaded_by') — `can_user_view()` below short-circuits
    # on `published_to_chapter` (and several other branches) before ever
    # touching `self.uploaded_by`, so most rows reach the template with
    # nothing warming the FK cache, and `documents.html` prints
    # `doc.uploaded_by.name` once per row — one query per document
    # otherwise. Caught live via the dev-mode query monitor: 13x the same
    # query shape for 13 documents.
    # select_related('committee') too — `can_user_view()`'s `committee_only`
    # branch touches `self.committee`, and without this every document
    # whose visibility check reaches that branch re-fetches the Committee
    # row we already have in `committee` above, one query per document.
    all_documents = CommitteeDocument.objects.filter(committee=committee).select_related('committee', 'uploaded_by')

    # Every document on this page belongs to the same `committee`, so
    # whether `user` is a member/chair of it is one fact, not one fact per
    # document. Compute both once and hand them to `can_user_view()` so its
    # `committee_only`/`chairs_only` branches don't each run their own
    # membership query per document (still just as correct for a
    # `visibility='custom'` document, which is genuinely per-document and
    # not precomputed here).
    user_is_committee_member = committee.members.filter(pk=user.pk).exists()
    user_is_committee_chair = committee.chairs.filter(pk=user.pk).exists()

    # Filter documents based on visibility permissions
    documents = [
        doc for doc in all_documents
        if doc.can_user_view(
            user,
            is_committee_member=user_is_committee_member,
            is_committee_chair=user_is_committee_chair,
        )
    ]

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
    # `committee.is_chair(user)` also grants exec-board members chair-level
    # access when `committee.is_exec_board` is set — `user_is_committee_chair`
    # above does not (it's the raw `chairs` check `can_user_view()` needs, and
    # changing THAT would change which documents are visible, not just how
    # many queries it costs). The two answers are only guaranteed identical
    # when `is_exec_board` is False, which is true for most committees this
    # view serves — skip the second, otherwise-redundant chairs-table query
    # in that common case; fall back to the real (memoizing) call when it
    # isn't. Half of the "×2 duplicate query" residue v3.31.0 flagged and
    # left for a follow-up.
    if committee.is_exec_board:
        is_chair = committee.is_chair(user)
    else:
        is_chair = user_is_committee_chair
        # Prime is_chair()'s own memo with the answer we already have, so
        # can_edit_committee_minutes() below — and anything else that calls
        # committee.is_chair(user) later in this request — gets it for free
        # instead of re-querying. Without this, skipping the call here just
        # moves the "second" query to whichever caller happens to run next.
        committee.prime_chair_memo(user, is_chair)
    can_delete = is_vp or is_chair
    can_edit_minutes = can_edit_committee_minutes(user, committee)

    # Unpublished minutes (draft/finalized, not yet turned into a
    # CommitteeDocument via publish_committee_minutes) aren't in
    # `documents` at all — they're a separate model until published. Show
    # them here too, so members don't have to know to check the separate
    # Minutes tab to find a session that hasn't been published yet. Same
    # permission gate as `committee_minutes_list` itself (member/chair/
    # officer/admin) — anyone who could see them on that page can see them
    # here.
    can_view_minutes = is_committee_member_or_above(
        user, committee, is_committee_member=user_is_committee_member,
    )
    unpublished_minutes = []
    if can_view_minutes:
        unpublished_minutes = list(
            ChapterMinutes.objects.filter(committee=committee)
            .exclude(status='published')
            .select_related('created_by')
        )
        # `can_edit_minutes` (computed above) already IS
        # `can_edit_committee_minutes(user, committee)` for this exact
        # user/committee pair — pass it through so the per-row call below
        # doesn't re-run that query once per unpublished minutes record.
        for m in unpublished_minutes:
            m.can_user_edit = can_edit_specific_minutes(user, committee, m, can_edit_any=can_edit_minutes)

    # Version history — only run when the feature is actually on, so a
    # chapter that never enables document_versioning pays nothing extra for
    # this page. Was `doc.versions.all()` inside a `for doc in documents`
    # loop: one query per document regardless of the select_related on it
    # (a per-instance reverse-FK lookup is still a per-instance lookup).
    # Caught by the same dev-mode monitor pass as the `uploaded_by` fix
    # above — one bulk query across every document's versions, grouped in
    # Python, same pattern `linked_minutes_map` already uses a few lines up
    # for the same reason.
    versioning_enabled = check_feature_enabled('document_versioning')
    if versioning_enabled:
        versions_by_doc = {}
        versions_qs = DocumentVersion.objects.filter(
            document_id__in=[doc.id for doc in documents]
        ).select_related('uploaded_by')
        for version in versions_qs:
            versions_by_doc.setdefault(version.document_id, []).append(version)
        for doc in documents:
            doc.version_history = versions_by_doc.get(doc.id, [])

    return render(request, "committee/documents.html", {
        "committee": committee,
        "documents": documents,
        "perm": perm,
        "can_delete": can_delete,
        "is_vp": is_vp,
        "is_chair": is_chair,
        "can_edit_minutes": can_edit_minutes,
        "versioning_enabled": versioning_enabled,
        "unpublished_minutes": unpublished_minutes,
    })