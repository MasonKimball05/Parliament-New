from django.shortcuts import render
from src.models import CommitteeDocument, Committee, ChapterFolder
from django.contrib.auth.decorators import login_required
from collections import defaultdict

@login_required
def chapter_documents(request):
    """View for displaying all documents published to the chapter, organized by folder"""
    documents = CommitteeDocument.objects.filter(published_to_chapter=True).select_related('committee', 'uploaded_by', 'chapter_folder')

    # Get all folders
    all_folders = ChapterFolder.objects.all()

    # Organize documents by folder - include ALL folders even if empty
    folders_with_documents = []
    uncategorized_documents = []

    # First, create dict of documents by folder
    docs_by_folder_id = defaultdict(list)
    for doc in documents:
        if doc.chapter_folder:
            docs_by_folder_id[doc.chapter_folder.id].append(doc)
        else:
            uncategorized_documents.append(doc)

    # Now pair each folder with its documents (empty list if no documents)
    for folder in all_folders.order_by('name'):
        folder_documents = docs_by_folder_id.get(folder.id, [])
        folders_with_documents.append((folder, folder_documents))

    # Check if user is officer (for folder management and uploads)
    is_officer = request.user.member_type == 'Officer'

    return render(request, 'chapter_documents.html', {
        'folders_with_documents': folders_with_documents,
        'uncategorized_documents': uncategorized_documents,
        'all_folders': all_folders,
        'total_documents': documents.count(),
        'is_officer': is_officer,
    })
