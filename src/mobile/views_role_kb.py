"""
The app's Role Knowledge Base (v4.4.0), the native version of the website's
``role_knowledge_base_index`` (/role-knowledge-base/) and
``role_knowledge_base`` (/officers/roles/<id>/knowledge/) pages. Read only;
editing and the revision history stay on the website.

* ``GET role-knowledge-base/?q=`` — every Role with its active holders and
  whether its page has been written, filtered by ``q`` exactly as the web's
  search box (role name, holders, the current revision's text). Same builder
  as the page, ``role_knowledge_base_index_data``.
* ``GET role-knowledge-base/<role_id>/`` — that role's current page. Same
  builder, ``role_knowledge_base_page_data``, so like the web this never
  creates a ``RoleKnowledgeBase`` row on read.

The content is plain text (a textarea; the web renders it with
``|linebreaks``), so it goes to the app as ``paragraphs``: the text split on
blank lines, as ``linebreaks`` splits it, single newlines kept inside each.
``content`` is the raw text too.

Access is the web's: any signed-in member (the pages are ``@login_required``
only, under view/officer but deliberately open — see the web module). No
``@require_page_enabled`` on either, so only the pledge restriction applies.
``can_edit`` is ``officer_required``'s rule, for the "Edit on the website" link.
"""
import re

from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import Role
from src.view.officer.role_knowledge_base import (
    role_knowledge_base_index_data,
    role_knowledge_base_page_data,
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
        ctx = role_knowledge_base_page_data(role, request.user)
        rev = ctx['current_revision']
        current = None
        if rev is not None:
            current = {
                'content': rev.content,
                'paragraphs': paragraphs(rev.content),
                'edited_by': rev.edited_by.name if rev.edited_by else None,
                'created_at': rev.created_at.isoformat(),
            }
        return Response({
            'id': role.id,
            'name': role.name,
            'current': current,
            'revision_count': ctx['revision_count'],
            'can_edit': ctx['can_edit'],
            'web': {
                'page': reverse('role_knowledge_base', kwargs={'role_id': role.id}),
                'history': reverse('role_knowledge_base_history', kwargs={'role_id': role.id}),
                'index': reverse('role_knowledge_base_index'),
            },
        })
