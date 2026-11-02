"""
The app's Role Knowledge Base (v4.4.0), the native version of the website's
``role_knowledge_base_index`` (/role-knowledge-base/) and
``role_knowledge_base`` (/officers/roles/<id>/knowledge/) pages, and (v4.5.0)
``role_knowledge_base_history`` and ``edit_role_knowledge_base``.

* ``GET role-knowledge-base/?q=`` — every Role with its active holders and
  whether its page has been written, filtered by ``q`` exactly as the web's
  search box (role name, holders, the current revision's text). Same builder
  as the page, ``role_knowledge_base_index_data``.
* ``GET role-knowledge-base/<role_id>/`` — that role's current page. Same
  builder, ``role_knowledge_base_page_data``, so like the web this never
  creates a ``RoleKnowledgeBase`` row on read.
* ``PUT role-knowledge-base/<role_id>/`` ``{content}`` — save a new revision
  (officers and chairs, ``officer_required``'s rule), through the web's own
  ``save_role_knowledge_base``: always a new revision, the page row made on
  this write only, logged. Answers with the page as ``GET`` does. The text is
  saved as typed (the web's textarea keeps blank text too).
* ``GET role-knowledge-base/<role_id>/history/`` — every revision, newest
  first, as ``role_knowledge_base_history`` lists them (any member).

The content is plain text (a textarea; the web renders it with
``|linebreaks``), so it goes to the app as ``paragraphs``: the text split on
blank lines, as ``linebreaks`` splits it, single newlines kept inside each.
``content`` is the raw text too.

Access is the web's: any signed-in member (the pages are ``@login_required``
only, under view/officer but deliberately open — see the web module). No
``@require_page_enabled`` on either, so only the pledge restriction applies.
``can_edit`` is ``officer_required``'s rule, which the ``PUT`` enforces.
"""
import re

from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView, check_web_page
from src.mobile.views_excuses_polls import BadRequest
from src.models import Role
from src.permissions import user_is_officer_or_chair
from src.view.officer.role_knowledge_base import (
    role_knowledge_base_index_data,
    role_knowledge_base_page_data,
    role_knowledge_base_revisions,
    save_role_knowledge_base,
)

_PARA_SPLIT = re.compile(r'\n{2,}')


def paragraphs(text):
    """``|linebreaks``'s paragraphs: newlines normalised, split on blank lines, empty ones dropped."""
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    return [p for p in _PARA_SPLIT.split(text) if p.strip()]


class RoleKnowledgeBaseIndexView(MobileAPIView):
    web_page = 'role_knowledge_base_index'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        query = (request.query_params.get('q') or '').strip()
        roles_data, total_roles = role_knowledge_base_index_data(query)
        return Response({
            'query': query,
            'total_roles': total_roles,
            'roles': [
                {
                    'id': row['role'].id,
                    'name': row['role'].name,
                    'holders': row['holders'],
                    'has_content': row['has_content'],
                }
                for row in roles_data
            ],
            'web': {'page': reverse('role_knowledge_base_index')},
        })


class RoleKnowledgeBaseView(MobileAPIView):
    web_page = 'role_knowledge_base'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request, role_id):
        role = get_object_or_404(Role, id=role_id)
        return Response(self._page(request, role))

    def put(self, request, role_id):
        # The edit is its own web page (``edit_role_knowledge_base``,
        # @officer_required, no toggle): its pledge restriction applies too.
        check_web_page(request.user, 'edit_role_knowledge_base', toggle=False)
        if not user_is_officer_or_chair(request.user):
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')
        role = get_object_or_404(Role, id=role_id)
        body = request.data if isinstance(request.data, dict) else {}
        content = body.get('content')
        if not isinstance(content, str):
            raise BadRequest('content is the page text.', code='invalid_input')
        save_role_knowledge_base(request, role, content)
        return Response(self._page(request, role))

    @staticmethod
    def _page(request, role):
        ctx = role_knowledge_base_page_data(role, request.user)
        rev = ctx['current_revision']
        return {
            'id': role.id,
            'name': role.name,
            'current': _revision_json(rev) if rev is not None else None,
            'revision_count': ctx['revision_count'],
            'can_edit': ctx['can_edit'],
            'web': {
                'page': reverse('role_knowledge_base', kwargs={'role_id': role.id}),
                'history': reverse('role_knowledge_base_history', kwargs={'role_id': role.id}),
                'index': reverse('role_knowledge_base_index'),
            },
        }


def _revision_json(rev):
    return {
        'content': rev.content,
        'paragraphs': paragraphs(rev.content),
        'edited_by': rev.edited_by.name if rev.edited_by else None,
        'created_at': rev.created_at.isoformat(),
    }


class RoleKnowledgeBaseHistoryView(MobileAPIView):
    web_page = 'role_knowledge_base_history'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request, role_id):
        role = get_object_or_404(Role, id=role_id)
        revisions = role_knowledge_base_revisions(role)
        return Response({
            'id': role.id,
            'name': role.name,
            # Newest first; the first is the current text (the page's "Current" badge).
            'revisions': [{'id': r.id, **_revision_json(r)} for r in revisions],
            'web': {'page': reverse('role_knowledge_base_history', kwargs={'role_id': role.id})},
        })
