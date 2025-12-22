from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import Committee, CommitteeDocument
from src.logging_utils import log_document_action, log_security_event, get_client_ip
import logging

logger = logging.getLogger('user_actions')

@login_required
def delete_committee_document(request, code, document_id):
    """Delete a committee document - only accessible to VPs and chairs"""
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)
    
    user = request.user
    
    # Check permissions - only VPs and chairs can delete
    is_vp = committee.is_vp(user)
    is_chair = committee.is_chair(user)
    
    if not (is_vp or is_chair):
        # Log unauthorized attempt
        log_security_event(
            event_type='UNAUTHORIZED_DOCUMENT_DELETE',
            user=user,
            details={
                'document_id': document_id,
                'document_title': document.title,
                'committee': code,
                'committee_name': committee.name
            },
            ip_address=get_client_ip(request),
            severity='WARNING'
        )
        
        logger.warning(
            f"Unauthorized delete attempt: User {user.name} (ID: {user.pk}) "
            f"tried to delete document '{document.title}' (ID: {document_id}) "
            f"from committee {committee.name}"
        )
        
        messages.error(request, 'Only committee VPs and chairs can delete documents.')
        return redirect('committee_documents', code=code)
    
    # Process deletion
    if request.method == 'POST':
        doc_title = document.title
        doc_id = document.id
        
        # Log the deletion
        log_document_action(
            action='DELETE',
            document=document,
            user=user,
            committee=committee
        )
        
        logger.info(
            f"Document deleted: '{doc_title}' (ID: {doc_id}) "
            f"from committee {committee.name} by {user.name} (ID: {user.pk})"
        )
        
        # Delete the actual file from storage
        if document.document:
            try:
                document.document.delete(save=False)
            except Exception as e:
                logger.error(
                    f"Error deleting file for document '{doc_title}' (ID: {doc_id}): {str(e)}"
                )
        
        # Delete the database record
        document.delete()
        
        messages.success(request, f'Document "{doc_title}" has been deleted successfully.')
    
    return redirect('committee_documents', code=code)
