from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from src.models import Committee, CommitteePermissions, CommitteeDocument, ChapterFolder
from django.contrib.auth.decorators import login_required
from django.contrib import messages

@login_required
def committee_upload_document(request, code):  # Make sure this says 'code' not 'id'
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can upload documents.')
        return redirect('committee_detail', code=code)

    # Get all folders for the upload form
    folders = ChapterFolder.objects.all()

    if request.method == 'POST':
        file = request.FILES.get('file')
        title = request.POST.get('title', '')
        description = request.POST.get('description', '')
        publish_to_chapter = request.POST.get('publish_to_chapter') == 'true'
        document_type = request.POST.get('document_type', 'general')
        meeting_date = request.POST.get('meeting_date', None)
        folder_id = request.POST.get('chapter_folder', None)

        if file and title:
            # Get folder if specified
            chapter_folder = None
            if folder_id and publish_to_chapter:
                try:
                    chapter_folder = ChapterFolder.objects.get(id=folder_id)
                except ChapterFolder.DoesNotExist:
                    pass

            CommitteeDocument.objects.create(
                committee=committee,
                title=title,
                document=file,
                uploaded_by=request.user,
                description=description,
                published_to_chapter=publish_to_chapter,
                chapter_folder=chapter_folder,
                document_type=document_type,
                meeting_date=meeting_date if meeting_date else None
            )
            if publish_to_chapter:
                messages.success(request, 'Document uploaded and published to chapter successfully.')
            else:
                messages.success(request, 'Document uploaded successfully.')
            return redirect('committee_documents', code=code)
        else:
            messages.error(request, 'Please provide both a file and a title.')

    return render(request, 'committee/upload_document.html', {
        'committee': committee,
        'folders': folders
    })