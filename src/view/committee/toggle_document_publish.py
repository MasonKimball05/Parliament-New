from django.http import HttpResponseForbidden
from django.shortcuts import redirect, get_object_or_404
from src.models import Committee, CommitteeDocument
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.logging_utils import log_document_action, log_security_event, get_client_ip

@login_required
def toggle_document_publish(request, code, document_id):
    """Toggle the published_to_chapter status of a document"""
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)

    # Check permissions - only chairs can toggle publish status
    if not committee.is_chair(request.user):
        # Log unauthorized attempt
        log_security_event(
            event_type='UNAUTHORIZED_DOCUMENT_PUBLISH',
            user=request.user,
            details={
                'document_id': document_id,
                'committee': code,
                'attempted_action': 'toggle_publish'
            },
            ip_address=get_client_ip(request),
            severity='WARNING'
        )
        messages.error(request, 'Only committee chairs can change document publish settings.')
        return redirect('committee_documents', code=code)

    # Toggle the publish status
    old_status = document.published_to_chapter
    document.published_to_chapter = not document.published_to_chapter
    document.save()

    # Log the action
    action = 'PUBLISH' if document.published_to_chapter else 'UNPUBLISH'
    log_document_action(
        action=action,
        document=document,
        user=request.user,
        committee=committee,
        details={
            'previous_status': old_status,
            'new_status': document.published_to_chapter
        }
    )

    if document.published_to_chapter:
        messages.success(request, f'"{document.title}" has been published to chapter documents.')
    else:
        messages.success(request, f'"{document.title}" has been unpublished from chapter documents.')

    return redirect('committee_documents', code=code)
