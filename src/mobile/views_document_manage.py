"""
Managing chapter documents in the app (v4.x, officer tools batch 5), through
the website pages' own helpers:

* ``GET officer/documents/`` — Manage Documents (``manage_chapter_documents``):
  published documents by folder, published ones in no folder, drafts, the
  folders, and the upload form's choices (folders, the committees this officer
  may file under, document types).
* ``POST officer/documents/`` multipart ``{file, title, description?,
  document_type?, chapter_folder?, committee?, publish_now?}`` — the upload
  page (``upload_chapter_document``), ``validate_uploaded_file`` and all. v4.4.2:
  build 19's file picker sends it; older builds open the website's upload
  page instead.
* ``GET officer/documents/<id>/`` — one document as its manage page shows it,
  with the committees it may move to; ``POST`` ``{action: update, title,
  description, document_type, committee, chapter_folder,
  published_to_chapter}`` or ``{action: delete}`` (``manage_chapter_document``).
  The website can't replace a document's file, so neither can the app.
* ``GET officer/documents/<id>/file/`` — the file, drafts included, for the
  officer who may manage it.
* ``POST officer/documents/folders/`` ``{folder_name, folder_description?}`` and
  ``POST officer/documents/folders/<id>/delete/`` — ``create_folder`` and
  ``delete_folder``, admins only.

The pages are ``officer_required`` (folders ``admin_required``), none with a
page toggle. Like the website, nothing here is logged or notified.
"""
import mimetypes
import os

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer_tools import _Officers
from src.models import ChapterFolder, CommitteeDocument
from src.utils.content_disposition import apply_disposition
from src.utils.file_validation import MAX_FILE_SIZE
from src.view.manage_chapter_document import (
    assignable_committees, can_manage_chapter_document, delete_chapter_document, update_chapter_document,
)
from src.view.manage_chapter_documents import managed_documents_context
from src.view.manage_folders import FolderRefused, create_chapter_folder, delete_chapter_folder
from src.view.upload_chapter_document import (
    DocumentUploadRefused, create_chapter_document, document_fields_error, upload_committees,
)

#: The upload page's type select (it leaves out "Meeting Agenda").
UPLOAD_TYPES = ('general', 'minutes', 'report', 'policy')
#: Its file input's ``accept`` list.
UPLOAD_EXTENSIONS = ['.pdf', '.docx', '.doc', '.xls', '.xlsx', '.ppt', '.pptx']


def _text(value):
    return '' if value is None else str(value)


def _committee_json(c):
    return {'id': c.id, 'name': c.name}


def _folder_json(f):
    return {'id': f.id, 'name': f.name, 'description': f.description or ''}


def _doc_json(d, user):
    name = os.path.basename(d.document.name or '')
    return {
        'id': d.id,
        'title': d.title,
        'description': d.description or '',
        'document_type': d.document_type,
        'type': d.get_document_type_display(),
        'published': d.published_to_chapter,
        'committee': _committee_json(d.committee) if d.committee_id else None,
        'folder': _folder_json(d.chapter_folder) if d.chapter_folder_id else None,
        'uploaded_at': d.uploaded_at.isoformat() if d.uploaded_at else None,
        'uploaded_by': d.uploaded_by.get_display_name() if d.uploaded_by_id else '',
        'file_name': name,
        'extension': os.path.splitext(name)[1].lstrip('.').lower(),
        'file': f'officer/documents/{d.id}/file/',
        # The list shows every chapter document; another committee's only its
        # chair (or an admin) may change.
        'can_manage': can_manage_chapter_document(user, d),
    }


def _types():
    return [{'value': v, 'label': label} for v, label in CommitteeDocument.DOCUMENT_TYPES]


def _managed(request, doc_id):
    document = get_object_or_404(CommitteeDocument.objects.select_related('committee', 'chapter_folder', 'uploaded_by'),
                                 id=doc_id)
    if not can_manage_chapter_document(request.user, document):
        raise exceptions.PermissionDenied("You don't have permission to manage this document.", code='forbidden')
    return document


class ManageDocumentsView(_Officers):
    web_page = 'manage_chapter_documents'
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request):
        user = request.user
        c = managed_documents_context(user)
        return Response({
            'folders': [{**_folder_json(folder), 'documents': [_doc_json(d, user) for d in docs]}
                        for folder, docs in c['folders_with_published_docs']],
            'uncategorized': [_doc_json(d, user) for d in c['uncategorized_published_docs']],
            'drafts': [_doc_json(d, user) for d in c['unpublished_docs']],
            'total_documents': c['total_documents'],
            'is_admin': c['is_admin'],
            'upload': {
                'committees': [_committee_json(x) for x in upload_committees(user)],
                'types': [t for t in _types() if t['value'] in UPLOAD_TYPES],
                'extensions': UPLOAD_EXTENSIONS,
                'max_bytes': MAX_FILE_SIZE,
            },
            'web': {'upload': reverse('upload_chapter_document'), 'manage': reverse('manage_chapter_documents')},
        })

    def post(self, request):
        # The upload page's own gate (same decorator as this list).
        data = request.data
        try:
            document, message = create_chapter_document(
                request.user,
                file=request.FILES.get('file'),
                title=_text(data.get('title')),
                description=_text(data.get('description')),
                document_type=_text(data.get('document_type') or 'general'),
                folder_id=data.get('chapter_folder') or None,
                committee_id=_text(data.get('committee')),
                publish_now=_text(data.get('publish_now')).lower() == 'true',
            )
        except DocumentUploadRefused as exc:
            raise BadRequest(str(exc), code='invalid_input')
        return Response({'document': _doc_json(document, request.user), 'message': message}, status=201)


class ManageDocumentView(_Officers):
    web_page = 'manage_chapter_document'
    parser_classes = [JSONParser]

    def get(self, request, doc_id):
        document = _managed(request, doc_id)
        return Response({
            'document': _doc_json(document, request.user),
            'folders': [_folder_json(f) for f in ChapterFolder.objects.order_by('name')],
            'committees': [_committee_json(x) for x in assignable_committees(request.user, document)],
            'types': _types(),
            'is_admin': bool(request.user.is_admin),
        })

    def post(self, request, doc_id):
        document = _managed(request, doc_id)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action')
        if action == 'delete':
            title = delete_chapter_document(document)
            return Response({'ok': True, 'deleted': True, 'message': f'Document "{title}" deleted successfully.'})
        if action != 'update':
            raise BadRequest('Unknown action.', code='invalid_input')
        # The web form posts every field as a string; so does this, a missing
        # title / description / type keeping the current one as there.
        data = {}
        for key in ('title', 'description', 'document_type', 'committee', 'chapter_folder'):
            if body.get(key) is not None:
                data[key] = _text(body[key])
        published = body.get('published_to_chapter')
        data['published_to_chapter'] = 'true' if published is True or _text(published).lower() == 'true' else ''
        if 'title' in data and not data['title'].strip():
            raise BadRequest('Give the document a title.', code='invalid_input')
        error = document_fields_error(data.get('title', document.title),
                                      data.get('document_type', document.document_type))
        if error:
            raise BadRequest(error, code='invalid_input')
        document = update_chapter_document(request.user, document, data)
        return Response({'ok': True, 'deleted': False, 'document': _doc_json(document, request.user),
                         'message': 'Document updated successfully!'})


class ManageDocumentFileView(_Officers):
    web_page = 'manage_chapter_document'

    def get(self, request, doc_id):
        document = _managed(request, doc_id)
        if not document.document:
            raise exceptions.NotFound()
        try:
            handle = document.document.open('rb')
        except (FileNotFoundError, OSError):
            raise exceptions.NotFound()
        name = os.path.basename(document.document.name)
        content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type)
        apply_disposition(response, content_type, name)   # RFC 5987 name, nosniff
        response['Cache-Control'] = 'private, no-store'
        return response


class _Admins(MobileAPIView):
    web_page_toggle = False
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Admins only.', code='forbidden')


class FolderCreateView(_Admins):
    web_page = 'create_folder'

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        try:
            folder = create_chapter_folder(request.user, _text(body.get('folder_name')),
                                           _text(body.get('folder_description')))
        except FolderRefused as exc:
            raise BadRequest(str(exc), code='invalid_input')
        return Response({'folder': _folder_json(folder), 'message': f'Folder "{folder.name}" created successfully!'},
                        status=201)


class FolderDeleteView(_Admins):
    web_page = 'delete_folder'

    def post(self, request, folder_id):
        name = delete_chapter_folder(get_object_or_404(ChapterFolder, id=folder_id))
        return Response({'ok': True, 'message': f'Folder "{name}" deleted successfully. '
                                                'Documents moved to "Uncategorized".'})
