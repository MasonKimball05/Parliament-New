from django.http import HttpResponseForbidden
from django.shortcuts import redirect, get_object_or_404
from src.models import Committee, CommitteeDocument
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from src.committee_management import refuse_document_publish, set_document_published
from src.logging_utils import get_client_ip

@login_required
@require_POST
def toggle_document_publish(request, code, document_id):
    """Toggle the published_to_chapter status of a document"""
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)

    # Check permissions - chairs, and members a chair has let manage documents
    from src.view.committee.member_permissions import can_manage_documents
    if not can_manage_documents(request.user, committee):
        # Log unauthorized attempt
        refuse_document_publish(request.user, document_id, committee, get_client_ip(request))
        messages.error(request, 'Only committee chairs can change document publish settings.')
        return redirect('committee_documents', code=code)

    # Toggle the publish status (and log it)
    set_document_published(document, request.user, committee, not document.published_to_chapter)

    if document.published_to_chapter:
        messages.success(request, f'"{document.title}" has been published to chapter documents.')
    else:
        messages.success(request, f'"{document.title}" has been unpublished from chapter documents.')

    return redirect('committee_documents', code=code)
