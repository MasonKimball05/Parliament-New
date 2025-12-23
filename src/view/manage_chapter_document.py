"""
View for managing chapter documents (edit, delete, publish/unpublish)
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import CommitteeDocument, ChapterFolder
from src.decorators import officer_required


@officer_required
def manage_chapter_document(request, doc_id):
    """Allow officers to edit/manage chapter documents"""
    document = get_object_or_404(CommitteeDocument, id=doc_id)

    # Get all folders for the dropdown
    folders = ChapterFolder.objects.all()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'delete':
            # Delete the document
            doc_title = document.title
            document.delete()
            messages.success(request, f'Document "{doc_title}" deleted successfully.')
            return redirect('manage_chapter_documents')

        elif action == 'update':
            # Update document metadata
            document.title = request.POST.get('title', document.title)
            document.description = request.POST.get('description', document.description)
            document.document_type = request.POST.get('document_type', document.document_type)

            # Update folder
            folder_id = request.POST.get('chapter_folder', None)
            if folder_id:
                try:
                    document.chapter_folder = ChapterFolder.objects.get(id=folder_id)
                except ChapterFolder.DoesNotExist:
                    document.chapter_folder = None
            else:
                document.chapter_folder = None

            # Update publish status
            document.published_to_chapter = request.POST.get('published_to_chapter') == 'true'

            document.save()
            messages.success(request, 'Document updated successfully!')
            return redirect('manage_chapter_documents')

    return render(request, 'manage_chapter_document.html', {
        'document': document,
        'folders': folders
    })
