from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from src.models import Committee, CommitteePermissions, CommitteeDocument, ChapterFolder
from src.models.documents import DocumentVersion
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from src.feature_flag_decorators import require_feature_flag
from src.utils.file_validation import validate_uploaded_file
from src.view.upload_chapter_document import document_fields_error


class CommitteeDocumentRefused(Exception):
    """Why an upload or a replacement wasn't saved: the page's error message (v4.4.2)."""


def create_committee_document(user, committee, *, file, title, description='', document_type='general',
                              meeting_date='', folder_id=None, publish_to_chapter=False):
    """
    Save a committee document upload by ``user`` (``committee_upload_document``'s
    POST; v4.4.2, shared with the app's src/mobile/views_committee_documents.py).
    The caller has checked ``can_manage_documents``. The file passes
    ``validate_uploaded_file``; the title and type must fit their columns and
    choices; a meeting date must be a date; a folder is only kept when it is
    published to the chapter (an unknown one means none). Returns ``(document,
    success message)``; raises ``CommitteeDocumentRefused``.
    """
    if file:
        try:
            validate_uploaded_file(file)
        except ValidationError as e:
            raise CommitteeDocumentRefused(f'File upload error: {str(e)}')

    if not (file and title):
        raise CommitteeDocumentRefused('Please provide both a file and a title.')
    # v4.4.2 — the page's inputs keep a browser inside these; a hand-made
    # request (or the app) would otherwise fail at the database.
    field_error = document_fields_error(title, document_type)
    if field_error:
        raise CommitteeDocumentRefused(field_error)
    meeting_day = None
    if meeting_date:
        try:
            meeting_day = parse_date(str(meeting_date))
        except ValueError:
            meeting_day = None
        if meeting_day is None:
            raise CommitteeDocumentRefused('Enter the meeting date as YYYY-MM-DD.')

    # Get folder if specified
    chapter_folder = None
    if folder_id and publish_to_chapter:
        try:
            chapter_folder = ChapterFolder.objects.get(id=folder_id)
        except (ChapterFolder.DoesNotExist, ValueError, TypeError):
            pass

    document = CommitteeDocument.objects.create(
        committee=committee,
        title=title,
        document=file,
        uploaded_by=user,
        description=description,
        published_to_chapter=publish_to_chapter,
        chapter_folder=chapter_folder,
        document_type=document_type,
        meeting_date=meeting_day,
    )
    if publish_to_chapter:
        return document, 'Document uploaded and published to chapter successfully.'
    return document, 'Document uploaded successfully.'


@login_required
def committee_upload_document(request, code):  # Make sure this says 'code' not 'id'
    committee = get_object_or_404(Committee, code=code)

    # Chairs, or members a chair has let manage documents.
    from src.view.committee.member_permissions import can_manage_documents
    if not can_manage_documents(request.user, committee):
        messages.error(request, 'Only committee chairs can upload documents.')
        return redirect('committee_home', code=code)

    # Get all folders for the upload form
    folders = ChapterFolder.objects.all()

    if request.method == 'POST':
        try:
            _document, message = create_committee_document(
                request.user, committee,
                file=request.FILES.get('file'),
                title=request.POST.get('title', ''),
                description=request.POST.get('description', ''),
                document_type=request.POST.get('document_type', 'general'),
                meeting_date=request.POST.get('meeting_date', ''),
                folder_id=request.POST.get('chapter_folder', None),
                publish_to_chapter=request.POST.get('publish_to_chapter') == 'true',
            )
        except CommitteeDocumentRefused as e:
            messages.error(request, str(e))
        else:
            messages.success(request, message)
            return redirect('committee_documents', code=code)

    return render(request, 'committee/upload_document.html', {
        'committee': committee,
        'folders': folders
    })


def replaced_message(document):
    """The success message after ``replace_committee_document``."""
    return (f'Uploaded a new version of "{document.title}" ({document.get_version_string()}). '
            f'The previous version is kept in its history.')


def replace_committee_document(user, document, new_file, change_notes=''):
    """
    Upload ``new_file`` as the next version of ``document`` by ``user``
    (``committee_replace_document``; v4.4.2, shared with the app's
    src/mobile/views_committee_documents.py). The caller has checked
    ``can_manage_documents`` and the ``document_versioning`` flag. The file
    passes ``validate_uploaded_file``; the file it replaces is archived as a
    ``DocumentVersion``. Raises ``CommitteeDocumentRefused``.
    """
    change_notes = (change_notes or '').strip()
    if not new_file:
        raise CommitteeDocumentRefused('Please choose a file to upload as the new version.')

    try:
        validate_uploaded_file(new_file)
    except ValidationError as e:
        raise CommitteeDocumentRefused(f'File upload error: {str(e)}')

    with transaction.atomic():
        # The snapshot records the file being REPLACED, at the version number
        # it held — not the incoming one. `change_notes` describes why this
        # version was retired, which is what a reader of the history wants to
        # know when they land on this row later.
        #
        # ⚠️ `file=document.document` passes an ALREADY-COMMITTED FieldFile.
        # `FileField.pre_save()` only re-saves (copies into storage under
        # `upload_to`) a file whose `_committed` is False — an already-saved
        # FieldFile is stored by reusing its existing `.name` as-is. So this
        # does NOT duplicate the bytes: the new DocumentVersion row and the
        # CommitteeDocument row it is archived from point at the SAME storage
        # path until `document.document` is reassigned below, at which point
        # the CommitteeDocument gets a genuinely new path and the version row
        # keeps the old one. That is why the archived version's file appears
        # to live under `committee_documents/`, not `document_versions/` —
        # `upload_to` only applies to files this call site actually uploads.
        # Do not "fix" this into an explicit copy; it would just double the
        # storage for every version kept.
        DocumentVersion.objects.create(
            document=document,
            version_number=document.version_number,
            file=document.document,
            uploaded_by=document.uploaded_by,
            change_notes=change_notes,
            file_size=document.document.size if document.document else None,
        )
        document.document = new_file
        document.version_number += 1
        document.uploaded_by = user
        document.uploaded_at = timezone.now()
        document.save(update_fields=['document', 'version_number', 'uploaded_by', 'uploaded_at'])

    return document


@login_required
@require_feature_flag('document_versioning')
@require_POST
def committee_replace_document(request, code, document_id):
    """
    Upload a new file for an existing CommitteeDocument, archiving the file
    it replaces as a DocumentVersion row instead of overwriting it in place.

    Gated on `document_versioning` because there is no "replace without
    history" fallback to fall back to — before this, replacing a document's
    file at all meant deleting it and uploading a new one under a new id.
    Off means the feature does not exist yet, not that it exists minus the
    history; there was never a bare-overwrite code path to preserve.
    """
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)

    from src.view.committee.member_permissions import can_manage_documents
    if not can_manage_documents(request.user, committee):
        messages.error(request, 'Only committee chairs can replace documents.')
        return redirect('committee_documents', code=code)

    try:
        replace_committee_document(request.user, document, request.FILES.get('file'),
                                   request.POST.get('change_notes', ''))
    except CommitteeDocumentRefused as e:
        messages.error(request, str(e))
        return redirect('committee_documents', code=code)

    messages.success(request, replaced_message(document))
    return redirect('committee_documents', code=code)