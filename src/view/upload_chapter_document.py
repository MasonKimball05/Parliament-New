"""
View for officers to upload documents directly to chapter (not through committee)
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import CommitteeDocument, ChapterFolder, Committee
from src.decorators import officer_required


@officer_required
def upload_chapter_document(request):
    """Allow officers to upload documents directly to chapter folders"""

    # Get or create a special "Chapter" committee for general chapter documents
    chapter_committee, created = Committee.objects.get_or_create(
        code='CHAPTER',
        defaults={
            'name': 'Chapter'
        }
    )

    # Get all folders
    folders = ChapterFolder.objects.all()

    if request.method == 'POST':
        file = request.FILES.get('file')
        title = request.POST.get('title', '')
        description = request.POST.get('description', '')
        document_type = request.POST.get('document_type', 'general')
        folder_id = request.POST.get('chapter_folder', None)
        publish_now = request.POST.get('publish_now') == 'true'

        if file and title:
            # Get folder if specified
            chapter_folder = None
            if folder_id:
                try:
                    chapter_folder = ChapterFolder.objects.get(id=folder_id)
                except ChapterFolder.DoesNotExist:
                    pass

            # Create the document
            CommitteeDocument.objects.create(
                committee=chapter_committee,
                title=title,
                document=file,
                uploaded_by=request.user,
                description=description,
                published_to_chapter=publish_now,
                chapter_folder=chapter_folder,
                document_type=document_type,
                meeting_date=None
            )

            if publish_now:
                if chapter_folder:
                    messages.success(request, f'Document published successfully to folder "{chapter_folder.name}"!')
                else:
                    messages.success(request, 'Document published successfully to Uncategorized!')
            else:
                messages.success(request, 'Document saved as draft. You can publish it from "Manage Documents".')

            return redirect('manage_chapter_documents')
        else:
            messages.error(request, 'Please provide both a file and a title.')

    return render(request, 'upload_chapter_document.html', {
        'folders': folders
    })
