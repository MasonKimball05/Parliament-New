"""
View for managing chapter documents (edit, delete, publish/unpublish)
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from src.models import CommitteeDocument, ChapterFolder, Committee
from src.decorators import officer_required


def can_manage_chapter_document(user, document):
    """
    Authorization: a committee-owned document may only be edited/deleted by an
    admin or that committee's chair. Chapter-level documents (no committee) may
    be managed by any officer (``officer_required`` gates the views).
    Without this any officer/chair could delete or edit ANOTHER committee's
    document — only the committee-reassignment branch was scoped.
    (07-22 auth security sweep, Finding B.)
    """
    return bool(user.is_admin
                or document.committee is None
                or document.committee.is_chair(user))


def assignable_committees(user, document):
    """
    The committees ``user`` may move ``document`` to: any active one for an
    admin, else those he chairs plus the document's current committee (so he
    can keep it there).
    """
    if user.is_admin:
        return list(Committee.objects.filter(is_active=True).order_by('name'))
    available = list(Committee.objects.filter(chairs=user, is_active=True).order_by('name'))
    if document.committee and document.committee not in available:
        available = available + [document.committee]
    return available


def update_chapter_document(user, document, data):
    """
    The page's "update" action (shared with the app's
    src/mobile/views_document_manage.py). ``data`` is the form's strings: a
    missing title, description or type keeps the current one; ``committee``
    blank means chapter-level and one ``user`` may not assign is ignored;
    ``chapter_folder`` blank or unknown means none; ``published_to_chapter``
    is ``'true'`` or not.
    """
    document.title = data.get('title', document.title)
    document.description = data.get('description', document.description)
    document.document_type = data.get('document_type', document.document_type)

    # Update committee (if user has permission)
    # Empty string means chapter-level (no committee)
    committee_id = (data.get('committee', '') or '').strip()
    if committee_id:
        try:
            new_committee = Committee.objects.get(id=committee_id)
            # Verify user can assign to this committee
            if user.is_admin or new_committee.is_chair(user) or new_committee == document.committee:
                document.committee = new_committee
        except (Committee.DoesNotExist, ValueError):
            pass
    else:
        # Set to chapter-level (no committee)
        document.committee = None

    # Update folder
    folder_id = data.get('chapter_folder', None)
    if folder_id:
        try:
            document.chapter_folder = ChapterFolder.objects.get(id=folder_id)
        except (ChapterFolder.DoesNotExist, ValueError):
            document.chapter_folder = None
    else:
        document.chapter_folder = None

    # Update publish status
    document.published_to_chapter = data.get('published_to_chapter') == 'true'

    document.save()
    return document


def delete_chapter_document(document):
    """The page's "delete" action; returns the document's title."""
    doc_title = document.title
    document.delete()
    return doc_title


@officer_required
def manage_chapter_document(request, doc_id):
    """Allow officers to edit/manage chapter documents"""
    document = get_object_or_404(CommitteeDocument, id=doc_id)

    # Guards both the GET (edit form) and the POST mutations.
    if not can_manage_chapter_document(request.user, document):
        return HttpResponseForbidden("You don't have permission to manage this document.")

    # Get all folders for the dropdown
    folders = ChapterFolder.objects.all().order_by('name')

    # Get committees user can assign document to
    # Admins can assign to any committee, chairs can assign to their committees
    is_admin = request.user.is_admin
    available_committees = assignable_committees(request.user, document)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'delete':
            # Delete the document
            doc_title = delete_chapter_document(document)
            messages.success(request, f'Document "{doc_title}" deleted successfully.')
            return redirect('manage_chapter_documents')

        elif action == 'update':
            # Update document metadata
            update_chapter_document(request.user, document, request.POST)
            messages.success(request, 'Document updated successfully!')
            return redirect('manage_chapter_documents')

    return render(request, 'manage_chapter_document.html', {
        'document': document,
        'folders': folders,
        'available_committees': available_committees,
        'is_admin': is_admin,
    })
