from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from src.models import Legislation, CommitteeDocument
import os
import urllib.parse


def get_file_type_info(file_path):
    """Determine file type information based on extension"""
    if not file_path:
        return {
            'is_pdf': False,
            'is_image': False,
            'is_office_doc': False,
            'file_extension': '',
        }

    ext = os.path.splitext(file_path)[1].lower()

    return {
        'is_pdf': ext == '.pdf',
        'is_image': ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'],
        'is_office_doc': ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp'],
        'file_extension': ext.upper().replace('.', '') if ext else 'Unknown',
    }


@login_required
def view_legislation_document(request, legislation_id):
    """View for displaying legislation documents in an embedded viewer"""
    legislation = get_object_or_404(Legislation, id=legislation_id)

    if not legislation.document:
        from django.http import Http404
        raise Http404("No document attached to this legislation")

    file_info = get_file_type_info(legislation.document.name)

    # Use relative URL - works on any host without localhost issues
    document_url = legislation.document.url

    context = {
        'document_url': document_url,
        'document_title': legislation.title,
        'document_type': 'Legislation Document',
        'back_url': reverse('legislation_detail', args=[legislation_id]),
        'document_description': legislation.description,
        'uploaded_by': legislation.posted_by.username if legislation.posted_by else None,
        'uploaded_at': legislation.created_at,
        **file_info,
    }

    return render(request, 'view_document.html', context)


@login_required
def view_chapter_document(request, document_id):
    """View for displaying chapter documents in an embedded viewer"""
    document = get_object_or_404(CommitteeDocument, id=document_id, published_to_chapter=True)

    # Check if user can view this document
    if not document.can_user_view(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You don't have permission to view this document")

    file_info = get_file_type_info(document.document.name)

    # Use relative URL - works on any host without localhost issues
    document_url = document.document.url

    context = {
        'document_url': document_url,
        'document_title': document.title,
        'document_type': document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Chapter Document',
        'back_url': reverse('chapter_documents'),
        'document_description': document.description,
        'uploaded_by': document.uploaded_by.username if document.uploaded_by else None,
        'uploaded_at': document.uploaded_at,
        **file_info,
    }

    return render(request, 'view_document.html', context)


@login_required
def view_committee_document(request, code, document_id):
    """View for displaying committee documents in an embedded viewer"""
    from src.models import Committee

    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)

    # Check if user can view this document
    if not document.can_user_view(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("You don't have permission to view this document")

    file_info = get_file_type_info(document.document.name)

    # Use relative URL - works on any host without localhost issues
    document_url = document.document.url

    context = {
        'document_url': document_url,
        'document_title': document.title,
        'document_type': document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Committee Document',
        'back_url': reverse('committee_documents', args=[code]),
        'document_description': document.description,
        'uploaded_by': document.uploaded_by.username if document.uploaded_by else None,
        'uploaded_at': document.uploaded_at,
        **file_info,
    }

    return render(request, 'view_document.html', context)


@login_required
def view_passed_legislation_document(request, pk):
    """View for displaying passed legislation documents in an embedded viewer"""
    legislation = get_object_or_404(Legislation, pk=pk, passed=True)

    if not legislation.document:
        from django.http import Http404
        raise Http404("No document attached to this legislation")

    file_info = get_file_type_info(legislation.document.name)

    # Use relative URL - works on any host without localhost issues
    document_url = legislation.document.url

    context = {
        'document_url': document_url,
        'document_title': legislation.title,
        'document_type': 'Passed Legislation',
        'back_url': reverse('passed_legislation_detail', args=[pk]),
        'document_description': legislation.description,
        'uploaded_by': legislation.posted_by.username if legislation.posted_by else None,
        'uploaded_at': legislation.created_at,
        **file_info,
    }

    return render(request, 'view_document.html', context)


# Mapping of document slugs to their details
REFERENCE_DOCUMENTS = {
    'constitution-bylaws': {
        'path': 'legislation_docs/Constitution and Bylaws of the Samford Chapter - August 2025.pdf',
        'title': 'Constitution & Bylaws of the Samford Chapter',
        'description': 'The official Constitution and Bylaws of the Samford Chapter of Beta Theta Pi',
        'back_url_name': 'constitution_bylaws',
    },
    'code-of-beta': {
        'path': 'legislation_docs/Code-of-Beta-Theta-Pi_44th-Edition_10.18.2022.pdf',
        'title': 'Code of Beta Theta Pi (44th Edition)',
        'description': 'The governing document of Beta Theta Pi Fraternity',
        'back_url_name': 'constitution_bylaws',
    },
    'kai-binder': {
        'path': 'legislation_docs/Kai-Binder.pdf',
        'title': 'Kai Committee Binder',
        'description': 'Complete procedures and guidelines for Kai Committee operations',
        'back_url_name': 'constitution_bylaws',
    },
    'trial-by-chapter': {
        'path': 'legislation_docs/Trial-By-Chapter-Overview.pdf',
        'title': 'Trial by Chapter Overview',
        'description': 'Process for severe violations requiring expulsion consideration',
        'back_url_name': 'constitution_bylaws',
    },
    'roberts-rules': {
        'path': 'legislation_docs/Roberts-Rules-of-Order.pdf',
        'title': "Robert's Rules of Order",
        'description': 'Parliamentary procedure guide for conducting meetings',
        'back_url_name': 'roberts_rules',
    },
}


@login_required
def view_reference_document(request, doc_slug):
    """View for displaying reference documents (constitution, bylaws, etc.) in an embedded viewer"""
    from django.http import Http404

    if doc_slug not in REFERENCE_DOCUMENTS:
        raise Http404("Document not found")

    doc_info = REFERENCE_DOCUMENTS[doc_slug]
    file_path = doc_info['path']

    # Use relative URL - works on any host without localhost issues
    document_url = settings.MEDIA_URL + file_path

    file_info = get_file_type_info(file_path)

    context = {
        'document_url': document_url,
        'document_title': doc_info['title'],
        'document_type': 'Reference Document',
        'back_url': reverse(doc_info['back_url_name']),
        'document_description': doc_info['description'],
        **file_info,
    }

    return render(request, 'view_document.html', context)
