"""
View for officers to manage all chapter documents (published and unpublished)
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from src.models import CommitteeDocument, Committee, ChapterFolder
from src.decorators import officer_required
from collections import defaultdict


@officer_required
def manage_chapter_documents(request):
    """View for officers to see and manage all chapter documents"""
    # Get the Chapter committee
    try:
        chapter_committee = Committee.objects.get(code='CHAPTER')
        documents = CommitteeDocument.objects.filter(committee=chapter_committee).select_related('uploaded_by', 'chapter_folder').order_by('-uploaded_at')
    except Committee.DoesNotExist:
        documents = CommitteeDocument.objects.none()

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
        'total_documents': documents.count(),
        'is_admin': is_admin,
    })
