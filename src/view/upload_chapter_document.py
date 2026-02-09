"""
View for officers to upload documents directly to chapter (not through committee)
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from src.models import CommitteeDocument, ChapterFolder, Committee
from src.decorators import officer_required
from src.utils.file_validation import validate_uploaded_file


@officer_required
def upload_chapter_document(request):
    """Allow officers to upload documents directly to chapter folders"""

    # Get all folders
    folders = ChapterFolder.objects.all().order_by('name')

    # Get committees user can upload to
    # Admins can upload to any committee, chairs can upload to their committees
    is_admin = request.user.is_admin
    if is_admin:
        available_committees = list(Committee.objects.filter(is_active=True).order_by('name'))
    else:
        # Get committees user is chair of
        available_committees = list(Committee.objects.filter(chairs=request.user, is_active=True).order_by('name'))

    if request.method == 'POST':
        file = request.FILES.get('file')
        title = request.POST.get('title', '')
        description = request.POST.get('description', '')
        document_type = request.POST.get('document_type', 'general')
        folder_id = request.POST.get('chapter_folder', None)
        committee_id = request.POST.get('committee', '')  # Empty string means chapter-level
        publish_now = request.POST.get('publish_now') == 'true'

        # Validate uploaded file
        if file:
            try:
                validate_uploaded_file(file)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'upload_chapter_document.html', {
                    'folders': folders,
                    'available_committees': available_committees,
                    'is_admin': is_admin,
                })

        if file and title:
            # Get committee (None for chapter-level documents)
            selected_committee = None
            if committee_id:
                try:
                    selected_committee = Committee.objects.get(id=committee_id)
                    # Verify permission (admins can upload to any, others need to be chair)
                    if not is_admin and not selected_committee.is_chair(request.user):
                        messages.error(request, 'You do not have permission to upload to that committee.')
                        return render(request, 'upload_chapter_document.html', {
                            'folders': folders,
                            'available_committees': available_committees,
                            'is_admin': is_admin,
                        })
                except Committee.DoesNotExist:
                    selected_committee = None

            # Get folder if specified
            chapter_folder = None
            if folder_id:
                try:
                    chapter_folder = ChapterFolder.objects.get(id=folder_id)
                except ChapterFolder.DoesNotExist:
                    pass

            # Create the document
            CommitteeDocument.objects.create(
                committee=selected_committee,  # None for chapter-level
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
                    location = selected_committee.name if selected_committee else 'Chapter Documents'
                    messages.success(request, f'Document published successfully under "{location}"!')
            else:
                messages.success(request, 'Document saved as draft. You can publish it from "Manage Documents".')

            return redirect('manage_chapter_documents')
        else:
            messages.error(request, 'Please provide both a file and a title.')

    return render(request, 'upload_chapter_document.html', {
        'folders': folders,
        'available_committees': available_committees,
        'is_admin': is_admin,
    })
