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


class DocumentUploadRefused(Exception):
    """Why ``create_chapter_document`` didn't save the upload (the page's error message)."""


def document_fields_error(title, document_type):
    """
    Why a document's title or type can't be saved, or ''. The page's inputs
    (a 200-character title, a select of ``DOCUMENT_TYPES``) keep a browser
    inside these; anything else would fail at the database.
    """
    if len(title or '') > CommitteeDocument._meta.get_field('title').max_length:
        return 'The title is too long (200 characters at most).'
    if document_type not in dict(CommitteeDocument.DOCUMENT_TYPES):
        return 'Choose a document type.'
    return ''


def upload_committees(user):
    """
    The committees ``user`` may file a document under: any active one for an
    admin, else the active committees he chairs. (None = chapter-level.)
    """
    if user.is_admin:
        return list(Committee.objects.filter(is_active=True).order_by('name'))
    return list(Committee.objects.filter(chairs=user, is_active=True).order_by('name'))


def create_chapter_document(user, *, file, title, description='', document_type='general',
                            folder_id=None, committee_id='', publish_now=False):
    """
    Save an upload as ``user`` (``upload_chapter_document``'s POST, shared with
    the app's src/mobile/views_document_manage.py): the file passes
    ``validate_uploaded_file``, a committee must be one he may file under (an
    unknown one means chapter-level, an unknown folder none), and it is a draft
    unless ``publish_now``. Returns ``(document, success message)``; raises
    ``DocumentUploadRefused``.
    """
    # Validate uploaded file
    if file:
        try:
            validate_uploaded_file(file)
        except ValidationError as e:
            raise DocumentUploadRefused(f'File upload error: {str(e)}')

    if not (file and title):
        raise DocumentUploadRefused('Please provide both a file and a title.')
    field_error = document_fields_error(title, document_type)
    if field_error:
        raise DocumentUploadRefused(field_error)

    # Get committee (None for chapter-level documents)
    selected_committee = None
    if committee_id:
        try:
            selected_committee = Committee.objects.get(id=committee_id)
        except (Committee.DoesNotExist, ValueError, TypeError):
            selected_committee = None
        # Verify permission (admins can upload to any, others need to be chair)
        if selected_committee and not user.is_admin and not selected_committee.is_chair(user):
            raise DocumentUploadRefused('You do not have permission to upload to that committee.')

    # Get folder if specified
    chapter_folder = None
    if folder_id:
        try:
            chapter_folder = ChapterFolder.objects.get(id=folder_id)
        except (ChapterFolder.DoesNotExist, ValueError, TypeError):
            pass

    # Create the document
    document = CommitteeDocument.objects.create(
        committee=selected_committee,  # None for chapter-level
        title=title,
        document=file,
        uploaded_by=user,
        description=description,
        published_to_chapter=publish_now,
        chapter_folder=chapter_folder,
        document_type=document_type,
        meeting_date=None
    )

    if publish_now:
        if chapter_folder:
            message = f'Document published successfully to folder "{chapter_folder.name}"!'
        else:
            location = selected_committee.name if selected_committee else 'Chapter Documents'
            message = f'Document published successfully under "{location}"!'
    else:
        message = 'Document saved as draft. You can publish it from "Manage Documents".'
    return document, message


@officer_required
def upload_chapter_document(request):
    """Allow officers to upload documents directly to chapter folders"""

    # Get all folders
    folders = ChapterFolder.objects.all().order_by('name')

    # Get committees user can upload to
    # Admins can upload to any committee, chairs can upload to their committees
    is_admin = request.user.is_admin
    available_committees = upload_committees(request.user)

    if request.method == 'POST':
        try:
            _document, message = create_chapter_document(
                request.user,
                file=request.FILES.get('file'),
                title=request.POST.get('title', ''),
                description=request.POST.get('description', ''),
                document_type=request.POST.get('document_type', 'general'),
                folder_id=request.POST.get('chapter_folder', None),
                committee_id=request.POST.get('committee', ''),  # Empty string means chapter-level
                publish_now=request.POST.get('publish_now') == 'true',
            )
        except DocumentUploadRefused as e:
            messages.error(request, str(e))
        else:
            messages.success(request, message)
            return redirect('manage_chapter_documents')

    return render(request, 'upload_chapter_document.html', {
        'folders': folders,
        'available_committees': available_committees,
        'is_admin': is_admin,
    })
