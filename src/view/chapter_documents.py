from django.shortcuts import render
from src.models import CommitteeDocument, Committee, ChapterFolder
from django.contrib.auth.decorators import login_required
from collections import defaultdict

@login_required
def chapter_documents(request):
    """View for displaying all documents published to the chapter, organized by folder"""
    documents = CommitteeDocument.objects.filter(published_to_chapter=True).select_related('committee', 'uploaded_by', 'chapter_folder')

    # Get all folders
    folders = ChapterFolder.objects.all()

    # Organize documents by folder
    documents_by_folder = defaultdict(list)
    uncategorized_documents = []

    for doc in documents:
        if doc.chapter_folder:
            documents_by_folder[doc.chapter_folder].append(doc)
        else:
            uncategorized_documents.append(doc)

    # Sort folders by name
    sorted_folders = sorted(documents_by_folder.items(), key=lambda x: x[0].name)

    # Check if user is officer (for folder management)
    is_officer = request.user.member_type == 'Officer'

    return render(request, 'chapter_documents.html', {
        'documents_by_folder': sorted_folders,
        'uncategorized_documents': uncategorized_documents,
        'all_folders': folders,
        'total_documents': documents.count(),
        'is_officer': is_officer,
    })
