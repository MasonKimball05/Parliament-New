from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseForbidden
from src.models import Legislation, CommitteeDocument
import os
import urllib.parse
import logging
import base64

logger = logging.getLogger(__name__)


def convert_pdf_to_images(file_path, max_pages=50, dpi=150):
    """
    Convert PDF pages to base64 images for mobile viewing.
    Returns a list of base64-encoded PNG images.
    """
    try:
        import fitz  # PyMuPDF

        images = []
        doc = fitz.open(file_path)

        # Limit pages to prevent memory issues
        num_pages = min(len(doc), max_pages)

        for page_num in range(num_pages):
            page = doc[page_num]
            # Render page to image at specified DPI
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)

            # Convert to base64
            img_data = pix.tobytes("png")
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            images.append({
                'data': img_base64,
                'page': page_num + 1,
                'width': pix.width,
                'height': pix.height,
            })

        doc.close()

        return {
            'images': images,
            'total_pages': len(doc) if hasattr(doc, '__len__') else num_pages,
            'truncated': num_pages < len(doc) if hasattr(doc, '__len__') else False,
        }
    except ImportError:
        logger.warning("PyMuPDF (fitz) library not installed, cannot convert PDF to images")
        return None
    except Exception as e:
        logger.error(f"Error converting PDF to images: {e}")
        return None


def convert_docx_to_html(file_path):
    """Convert a DOCX file to HTML using mammoth and clean up the output"""
    try:
        import mammoth
        import re

        with open(file_path, 'rb') as docx_file:
            result = mammoth.convert_to_html(docx_file)
            html = result.value

            # Strip all inline style attributes that might override our CSS
            html = re.sub(r'\s*style="[^"]*"', '', html)

            # Strip class attributes too since they might have unwanted styles
            html = re.sub(r'\s*class="[^"]*"', '', html)

            # Preserve tabs by converting them to a span with tab styling
            html = html.replace('\t', '<span class="docx-tab"></span>')

            # Preserve multiple spaces
            html = re.sub(r'  +', lambda m: '&nbsp;' * len(m.group()), html)

            return html
    except ImportError:
        logger.warning("mammoth library not installed, cannot preview DOCX files")
        return None
    except Exception as e:
        logger.error(f"Error converting DOCX to HTML: {e}")
        return None


def get_file_type_info(file_path):
    """Determine file type information based on extension"""
    if not file_path:
        return {
            'is_pdf': False,
            'is_image': False,
            'is_office_doc': False,
            'is_docx': False,
            'is_text': False,
            'file_extension': '',
        }

    ext = os.path.splitext(file_path)[1].lower()

    return {
        'is_pdf': ext == '.pdf',
        'is_image': ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'],
        'is_office_doc': ext in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp'],
        'is_docx': ext == '.docx',
        'is_text': ext in ['.txt', '.md', '.csv', '.log', '.json', '.xml', '.html', '.css', '.js', '.py'],
        'file_extension': ext.upper().replace('.', '') if ext else 'Unknown',
    }


def read_text_file(file_path, max_size=500000):
    """Read a text file and return its content, truncated if too large"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read(max_size)
            if len(content) == max_size:
                content += '\n\n... [File truncated - download for full content]'
            return content
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='latin-1') as f:
                content = f.read(max_size)
                if len(content) == max_size:
                    content += '\n\n... [File truncated - download for full content]'
                return content
        except Exception as e:
            logger.error(f"Error reading text file: {e}")
            return None
    except Exception as e:
        logger.error(f"Error reading text file: {e}")
        return None


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

    # Convert DOCX to HTML if applicable
    docx_html = None
    if file_info.get('is_docx'):
        docx_html = convert_docx_to_html(legislation.document.path)

    # Convert PDF to images for mobile viewing
    pdf_images = None
    if file_info.get('is_pdf'):
        pdf_images = convert_pdf_to_images(legislation.document.path)

    # Read text file content if applicable
    text_content = None
    if file_info.get('is_text'):
        text_content = read_text_file(legislation.document.path)

    context = {
        'document_url': document_url,
        'document_title': legislation.title,
        'document_type': 'Legislation Document',
        'back_url': reverse('legislation_detail', args=[legislation_id]),
        'document_description': legislation.description,
        'uploaded_by': legislation.posted_by.username if legislation.posted_by else None,
        'uploaded_at': legislation.created_at,
        'docx_html': docx_html,
        'pdf_images': pdf_images,
        'text_content': text_content,
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

    # Convert DOCX to HTML if applicable
    docx_html = None
    if file_info.get('is_docx'):
        docx_html = convert_docx_to_html(document.document.path)

    # Convert PDF to images for mobile viewing
    pdf_images = None
    if file_info.get('is_pdf'):
        pdf_images = convert_pdf_to_images(document.document.path)

    # Read text file content if applicable
    text_content = None
    if file_info.get('is_text'):
        text_content = read_text_file(document.document.path)

    context = {
        'document_url': document_url,
        'document_title': document.title,
        'document_type': document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Chapter Document',
        'back_url': reverse('chapter_documents'),
        'document_description': document.description,
        'uploaded_by': document.uploaded_by.username if document.uploaded_by else None,
        'uploaded_at': document.uploaded_at,
        'docx_html': docx_html,
        'pdf_images': pdf_images,
        'text_content': text_content,
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

    # Convert DOCX to HTML if applicable
    docx_html = None
    if file_info.get('is_docx'):
        docx_html = convert_docx_to_html(document.document.path)

    # Convert PDF to images for mobile viewing
    pdf_images = None
    if file_info.get('is_pdf'):
        pdf_images = convert_pdf_to_images(document.document.path)

    # Read text file content if applicable
    text_content = None
    if file_info.get('is_text'):
        text_content = read_text_file(document.document.path)

    context = {
        'document_url': document_url,
        'document_title': document.title,
        'document_type': document.get_document_type_display() if hasattr(document, 'get_document_type_display') else 'Committee Document',
        'back_url': reverse('committee_documents', args=[code]),
        'document_description': document.description,
        'uploaded_by': document.uploaded_by.username if document.uploaded_by else None,
        'uploaded_at': document.uploaded_at,
        'docx_html': docx_html,
        'pdf_images': pdf_images,
        'text_content': text_content,
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

    # Convert DOCX to HTML if applicable
    docx_html = None
    if file_info.get('is_docx'):
        docx_html = convert_docx_to_html(legislation.document.path)

    # Convert PDF to images for mobile viewing
    pdf_images = None
    if file_info.get('is_pdf'):
        pdf_images = convert_pdf_to_images(legislation.document.path)

    # Read text file content if applicable
    text_content = None
    if file_info.get('is_text'):
        text_content = read_text_file(legislation.document.path)

    context = {
        'document_url': document_url,
        'document_title': legislation.title,
        'document_type': 'Passed Legislation',
        'back_url': reverse('passed_legislation_detail', args=[pk]),
        'document_description': legislation.description,
        'uploaded_by': legislation.posted_by.username if legislation.posted_by else None,
        'uploaded_at': legislation.created_at,
        'docx_html': docx_html,
        'pdf_images': pdf_images,
        'text_content': text_content,
        **file_info,
    }

    return render(request, 'view_document.html', context)


@login_required
def download_legislation_document(request, legislation_id):
    """Protected download for legislation documents — enforces authentication."""
    legislation = get_object_or_404(Legislation, id=legislation_id)
    if not legislation.document:
        raise Http404("No document attached to this legislation")
    file_path = legislation.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


@login_required
def download_chapter_document(request, document_id):
    """Protected download for chapter documents — enforces authentication and permissions."""
    document = get_object_or_404(CommitteeDocument, id=document_id, published_to_chapter=True)
    if not document.can_user_view(request.user):
        return HttpResponseForbidden("You don't have permission to download this document")
    file_path = document.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


@login_required
def download_committee_document(request, code, document_id):
    """Protected download for committee documents — enforces authentication and permissions."""
    from src.models import Committee
    committee = get_object_or_404(Committee, code=code)
    document = get_object_or_404(CommitteeDocument, id=document_id, committee=committee)
    if not document.can_user_view(request.user):
        return HttpResponseForbidden("You don't have permission to download this document")
    file_path = document.document.path
    if not os.path.exists(file_path):
        raise Http404("File not found")
    filename = os.path.basename(file_path)
    return FileResponse(open(file_path, 'rb'), as_attachment=True, filename=filename)


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

    # Convert PDF to images for mobile viewing
    pdf_images = None
    if file_info.get('is_pdf'):
        full_path = os.path.join(settings.MEDIA_ROOT, file_path)
        if os.path.exists(full_path):
            pdf_images = convert_pdf_to_images(full_path)

    context = {
        'document_url': document_url,
        'document_title': doc_info['title'],
        'document_type': 'Reference Document',
        'back_url': reverse(doc_info['back_url_name']),
        'document_description': doc_info['description'],
        'pdf_images': pdf_images,
        **file_info,
    }

    return render(request, 'view_document.html', context)
