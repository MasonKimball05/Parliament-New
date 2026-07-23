"""
View for managing chapter documents (edit, delete, publish/unpublish)
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from src.models import CommitteeDocument, ChapterFolder, Committee
from src.decorators import officer_required


@officer_required
def manage_chapter_document(request, doc_id):
    """Allow officers to edit/manage chapter documents"""
    document = get_object_or_404(CommitteeDocument, id=doc_id)

    # Authorization: a committee-owned document may only be edited/deleted by an
    # admin or that committee's chair. Chapter-level documents (no committee) may
    # be managed by any officer (@officer_required already gates this view).
    # Without this guard any officer/chair could delete or edit ANOTHER
    # committee's document — only the committee-reassignment branch below was
    # scoped. Guards both the GET (edit form) and the POST mutations.
    # (07-22 auth security sweep, Finding B.)
    if not (request.user.is_admin
            or document.committee is None
            or document.committee.is_chair(request.user)):
        return HttpResponseForbidden("You don't have permission to manage this document.")

    # Get all folders for the dropdown
    folders = ChapterFolder.objects.all().order_by('name')

    # Get committees user can assign document to
    # Admins can assign to any committee, chairs can assign to their committees
    is_admin = request.user.is_admin
    if is_admin:
        available_committees = list(Committee.objects.filter(is_active=True).order_by('name'))
    else:
        # Get committees user is chair of
        available_committees = list(Committee.objects.filter(chairs=request.user, is_active=True).order_by('name'))
        # Also include the document's current committee so they can keep it there
        if document.committee and document.committee not in available_committees:
            available_committees = available_committees + [document.committee]

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

            # Update committee (if user has permission)
            # Empty string means chapter-level (no committee)
            committee_id = request.POST.get('committee', '').strip()
            if committee_id:
                try:
                    new_committee = Committee.objects.get(id=committee_id)
                    # Verify user can assign to this committee
                    if is_admin or new_committee.is_chair(request.user) or new_committee == document.committee:
                        document.committee = new_committee
                except Committee.DoesNotExist:
                    pass
            else:
                # Set to chapter-level (no committee)
                document.committee = None

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
        'folders': folders,
        'available_committees': available_committees,
        'is_admin': is_admin,
    })
