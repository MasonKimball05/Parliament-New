"""
Uploading and replacing a committee's documents in the app (v4.4.2, build 19's
file picker): the website's Upload Document page and its "Replace File"
dialog, saved by the same helpers the web views call
(``create_committee_document`` / ``replace_committee_document`` in
src/view/committee/upload_document.py), so ``validate_uploaded_file``, the
title and type checks and the version history are the website's.

* ``GET committees/<code>/documents/upload/`` — the form's choices: the
  document types, the chapter folders (kept only when it's published to the
  chapter), the file input's ``accept`` list, the size limit, and whether
  replacing a file is on (``document_versioning``).
* ``POST committees/<code>/documents/upload/`` multipart ``{file, title,
  description?, document_type?, meeting_date? (YYYY-MM-DD),
  publish_to_chapter? ("true"), chapter_folder?}``. 201 ``{document,
  message}``.
* ``POST committees/<code>/documents/<id>/replace/`` multipart ``{file,
  change_notes?}`` — a new version; the old file is kept in its history. 403
  ``feature_disabled`` while ``document_versioning`` is off (the web route is
  ``@require_feature_flag``). 200 ``{document, message}``.

Who: ``can_manage_documents`` (chairs and members a chair has designated), as
both web views. A refused file or field is 400 ``invalid_input`` with the
website's message. Neither web page has a page toggle.
"""
from django.http import Http404
from rest_framework import exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _document_json
from src.mobile.views_excuses_polls import BadRequest
from src.models import ChapterFolder, Committee, CommitteeDocument
from src.utils.file_validation import MAX_FILE_SIZE
from src.view.committee.member_permissions import can_manage_documents
from src.view.committee.upload_document import (
    CommitteeDocumentRefused, create_committee_document, replace_committee_document, replaced_message,
)

#: The upload page's file input ``accept`` list (committee/upload_document.html).
UPLOAD_EXTENSIONS = ['.pdf', '.docx', '.doc', '.txt']


def _text(value):
    return '' if value is None else str(value)


def versioning_on():
    return check_feature_enabled('document_versioning')


class _CommitteeDocumentUpload(MobileAPIView):
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def committee(self, request, code, message):
        committee = Committee.objects.filter(code=code).first()
        if committee is None:
            raise Http404
        if not can_manage_documents(request.user, committee):
            raise exceptions.PermissionDenied(message, code='no_access')
        return committee


class CommitteeDocumentUploadView(_CommitteeDocumentUpload):
    web_page = 'upload_document'

    def get(self, request, code):
        self.committee(request, code, 'Only committee chairs can upload documents.')
        return Response({
            'types': [{'value': v, 'label': label} for v, label in CommitteeDocument.DOCUMENT_TYPES],
            'folders': [{'id': f.id, 'name': f.name} for f in ChapterFolder.objects.order_by('name')],
            'extensions': UPLOAD_EXTENSIONS,
            'max_bytes': MAX_FILE_SIZE,
            'can_replace': versioning_on(),
        })

    def post(self, request, code):
        committee = self.committee(request, code, 'Only committee chairs can upload documents.')
        data = request.data
        try:
            document, message = create_committee_document(
                request.user, committee,
                file=request.FILES.get('file'),
                title=_text(data.get('title')).strip(),
                description=_text(data.get('description')),
                document_type=_text(data.get('document_type') or 'general'),
                meeting_date=_text(data.get('meeting_date')).strip(),
                folder_id=data.get('chapter_folder') or None,
                publish_to_chapter=_text(data.get('publish_to_chapter')).lower() == 'true',
            )
        except CommitteeDocumentRefused as exc:
            raise BadRequest(str(exc), code='invalid_input')
        return Response({'document': _document_json(document, code), 'message': message}, status=201)


class CommitteeDocumentReplaceView(_CommitteeDocumentUpload):
    web_page = 'committee_replace_document'

    def post(self, request, code, document_id):
        if not versioning_on():
            raise exceptions.PermissionDenied('Replacing a document’s file is off.', code='feature_disabled')
        committee = self.committee(request, code, 'Only committee chairs can replace documents.')
        document = CommitteeDocument.objects.filter(pk=document_id, committee=committee).first()
        if document is None:
            raise Http404
        try:
            replace_committee_document(request.user, document, request.FILES.get('file'),
                                       _text(request.data.get('change_notes')))
        except CommitteeDocumentRefused as exc:
            raise BadRequest(str(exc), code='invalid_input')
        return Response({'document': _document_json(document, code), 'message': replaced_message(document)})
