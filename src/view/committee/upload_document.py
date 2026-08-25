from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST
from src.models import Committee, CommitteePermissions, CommitteeDocument, ChapterFolder
from src.models.documents import DocumentVersion
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from src.feature_flag_decorators import require_feature_flag
from src.utils.file_validation import validate_uploaded_file

@login_required
def committee_upload_document(request, code):  # Make sure this says 'code' not 'id'
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can upload documents.')
        return redirect('committee_home', code=code)

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

        # Validate uploaded file
        if file:
            try:
                validate_uploaded_file(file)
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return render(request, 'committee/upload_document.html', {
                    'committee': committee,
                    'folders': folders
                })

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

    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can replace documents.')
        return redirect('committee_documents', code=code)

    new_file = request.FILES.get('file')
    change_notes = request.POST.get('change_notes', '').strip()

    if not new_file:
        messages.error(request, 'Please choose a file to upload as the new version.')
        return redirect('committee_documents', code=code)

    try:
        validate_uploaded_file(new_file)
    except ValidationError as e:
        messages.error(request, f'File upload error: {str(e)}')
        return redirect('committee_documents', code=code)

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
        document.uploaded_by = request.user
        document.uploaded_at = timezone.now()
        document.save(update_fields=['document', 'version_number', 'uploaded_by', 'uploaded_at'])

    messages.success(
        request,
        f'Uploaded a new version of "{document.title}" ({document.get_version_string()}). '
        f'The previous version is kept in its history.',
    )
    return redirect('committee_documents', code=code)