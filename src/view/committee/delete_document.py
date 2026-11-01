from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeDocument
from src.committee_management import (
    delete_committee_document as delete_committee_document_and_file, refuse_document_delete,
)
from src.logging_utils import get_client_ip

@login_required
def delete_committee_document(request, code, document_id):
    """Delete a committee document - only accessible to VPs and chairs"""
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)
    
    user = request.user
    
    # Check permissions - VPs, chairs, and members a chair has let manage documents
    from src.view.committee.member_permissions import can_manage_documents

    if not can_manage_documents(user, committee):
        # Log unauthorized attempt
        refuse_document_delete(user, document, committee, get_client_ip(request))

        messages.error(request, 'Only committee VPs and chairs can delete documents.')
        return redirect('committee_documents', code=code)
    
    # Process deletion
    if request.method == 'POST':
        # Logs, then deletes the file and the record (src/committee_management).
        doc_title = delete_committee_document_and_file(document, user, committee)

        messages.success(request, f'Document "{doc_title}" has been deleted successfully.')
    
    return redirect('committee_documents', code=code)
