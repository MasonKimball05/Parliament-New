"""
View for officers to manage all chapter documents (published and unpublished)
"""
from django.db.models import Q
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import CommitteeDocument, Committee, ChapterFolder
from src.decorators import officer_required
from collections import defaultdict
from src.models.users import member_defer


@officer_required
def manage_chapter_documents(request):
    """View for officers to see and manage all chapter documents"""
    # A "chapter document" here is any of: chapter-level (committee is
    # None — what upload_chapter_document.py creates when no committee is
    # picked from its dropdown, and what manage_chapter_document.py's
    # "update" action sets when the committee field is cleared), published
    # to the chapter regardless of which committee owns it (so this page
    # shows everything that's actually visible on the public
    # chapter_documents page), or explicitly tied to the Committee row
    # flagged is_chapter_committee=True (kept for any document that really
    # does carry that FK).
    #
    # ⚠️ v3.29.32 fix: this used to filter ONLY on `committee=chapter_committee`
    # — a Committee ROW, not the same concept as "chapter-level" (committee
    # is None) that the rest of this feature (upload + edit views) actually
    # uses. Nothing the upload form creates by default ever matched that
    # filter, so a chapter-level draft — the normal case — was invisible on
    # this page from the moment it was uploaded; only "Custom Folders"
    # (queried unconditionally, below) ever rendered. Found live 09-08-26:
    # Mason uploaded two unpublished test documents and could not find them
    # here to delete them.
    #
    # Deliberately excluded: an unpublished draft belonging to some OTHER
    # committee. That's the owning committee's own draft to manage until
    # it's actually published — this page isn't meant to surface every
    # committee's private in-progress documents to every officer.
    doc_filter = Q(committee__isnull=True) | Q(published_to_chapter=True)
    try:
        chapter_committee = Committee.objects.get(is_chapter_committee=True)
        doc_filter |= Q(committee=chapter_committee)
    except Committee.DoesNotExist:
        pass

    documents = CommitteeDocument.objects.filter(doc_filter).select_related('uploaded_by', 'chapter_folder', 'committee').defer(*member_defer('uploaded_by')).order_by('-uploaded_at')

    # Get all folders
    all_folders = ChapterFolder.objects.all()

    # Separate published and unpublished documents
    published_docs = []
    unpublished_docs = []

    # Organize published documents by folder
    docs_by_folder_id = defaultdict(list)

    for doc in documents:
        if doc.published_to_chapter:
            published_docs.append(doc)
            if doc.chapter_folder:
                docs_by_folder_id[doc.chapter_folder.id].append(doc)
        else:
            unpublished_docs.append(doc)

    # Create folders_with_documents list for published docs
    folders_with_published_docs = []
    uncategorized_published_docs = []

    for doc in published_docs:
        if not doc.chapter_folder:
            uncategorized_published_docs.append(doc)

    for folder in all_folders.order_by('name'):
        folder_documents = docs_by_folder_id.get(folder.id, [])
        folders_with_published_docs.append((folder, folder_documents))

    # Check if user is admin (for folder management)
    is_admin = request.user.is_admin

    return render(request, 'manage_chapter_documents.html', {
        'folders_with_published_docs': folders_with_published_docs,
        'uncategorized_published_docs': uncategorized_published_docs,
        'unpublished_docs': unpublished_docs,
        'all_folders': all_folders,
        # len(), not .count() — `documents` was already iterated (and its
        # results cached) by the for loop above; .count() would fire a
        # second, redundant SELECT COUNT(*) instead of reusing that cache.
        'total_documents': len(documents),
        'is_admin': is_admin,
    })
