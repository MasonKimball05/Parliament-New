"""
The app's Constitution & Bylaws reader (mobile plan, Phase 3g).

* ``GET cnb/?as_of=YYYY-MM-DD`` — every governing document members can see
  (``GoverningDocument.enabled()``), article by article, section by section,
  with the same suspension, partial-suspension and amendment-protection notes
  as the Document tab of ``cnb_viewer``. Each section carries the spans of its
  cross-references that resolve (``src/cnb_crossrefs.py``) so the app can link
  them, exactly as the website's ``cnb_linked`` tag does. With ``as_of`` (a
  past or today's date), the documents as they read at the end of that day,
  as the viewer's "as of" picker shows them (``documents_as_of``): sections
  added since are left out, a later change shows the older text with
  ``changed_since``. ``as_of`` echoes the date (null for the current text). A
  malformed or future date is a 400 (the page quietly shows the current text).
* ``GET cnb/sections/<id>/history/`` — ``cnb_section_history``: the current
  text, then every earlier version newest first, each with what replaced it
  (a resolution, a direct edit, an import) and the word diff to the text that
  replaced it, as ``[{op, text}]`` segments (``equal`` / ``delete`` /
  ``insert``) the app colours. Same helpers as the page
  (``history_section_or_404``, ``section_versions``).
* ``GET cnb/resolutions/`` — the Resolutions tab: every resolution, newest first.
* ``GET cnb/resolutions/<id>/`` — one resolution, read-only
  (``resolution_detail``): its clauses, body, notes and amendments.

The resolution builder (drafting, editing, amendments, structure changes,
status, collaborators) is ``views_cnb_resolutions.py``; the resolution's
response carries its status buttons, structure changes and collaborators
(``builder_payload``). The print view, the document preview and the PDF stay
on the website. The manager and resolution notes are ``views_cnb_manage.py``.
"""
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.response import Response

from src.cnb_crossrefs import Structure, anchor_for, find_references
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import Resolution, ResolutionNote
from src.view.cnb_history import (
    documents_as_of, history_section_or_404, parse_as_of, section_anchor, section_versions,
)
from src.view.officer.cnb import _can_edit_resolution


def _date(d):
    return d.isoformat() if d else None


def _refs(text, doc_type, structure):
    """Resolvable cross-references in ``text`` as ``{start, end, anchor}`` (UTF-16 safe: see below)."""
    out = []
    for ref in find_references(text, doc_type):
        if structure.exists(ref):
            # The app indexes strings in UTF-16 code units; Python in code
            # points. Section text is almost all ASCII, but a Greek letter or
            # curly quote before the reference would shift every offset after
            # it, so convert.
            out.append({
                'start': len(text[:ref.start].encode('utf-16-le')) // 2,
                'end': len(text[:ref.end].encode('utf-16-le')) // 2,
                'anchor': anchor_for(ref.doc, ref.art, ref.sec),
            })
    return out


def _section_json(section, doc_type, structure):
    return {
        'id': section.id,
        'number': section.number,
        'title': section.title,
        'content': section.content,
        'refs': _refs(section.content, doc_type, structure),
        'active': section.is_active,
        'suspension_reason': '' if section.is_active else section.deactivation_reason,
        'partial_suspensions': [
            {'ref': p.get('ref', ''), 'reason': p.get('reason', ''), 'suspended_at': p.get('suspended_at', '')}
            for p in (section.partial_suspensions or []) if isinstance(p, dict)
        ],
        'protected_until': _date(section.protected_until) if section.amendment_protected else None,
        'protection_note': section.protection_note if section.amendment_protected else '',
        'history_path': reverse('cnb_section_history', kwargs={'section_id': section.pk}),
        'changed_since': bool(getattr(section, 'as_of_changed', False)),
    }


class CnbDocumentsView(MobileAPIView):
    web_page = 'constitution_bylaws'
    web_page_toggle = False   # cnb_viewer has no @require_page_enabled

    def get(self, request):
        raw = (request.query_params.get('as_of') or '').strip()
        as_of = parse_as_of(raw) if raw else None
        if raw and as_of is None:
            raise BadRequest('as_of must be a date (YYYY-MM-DD) no later than today.', code='invalid_input')
        documents = documents_as_of(as_of)
        structure = Structure.from_documents(documents)
        out = []
        for doc in documents:
            articles = list(doc.articles.all())
            out.append({
                'doc_type': doc.doc_type,
                'label': doc.get_doc_type_display(),
                'title': doc.title,
                'preamble': doc.preamble,
                'prose_only': not articles,
                'last_reviewed': _date(doc.last_reviewed),
                'articles': [{
                    'id': a.id,
                    'number': a.number,
                    'title': a.title,
                    'anchor': anchor_for(doc.doc_type, a.number),
                    'active': a.is_active,
                    'suspension_reason': '' if a.is_active else a.deactivation_reason,
                    'sections': [_section_json(s, doc.doc_type, structure) for s in a.sections.all()
                                 if not getattr(s, 'as_of_hidden', False)],
                } for a in articles],
            })
        user = request.user
        return Response({
            'documents': out,
            'as_of': as_of.date().isoformat() if as_of else None,
            'can_create_resolution': user.can_create_resolution,
            'can_manage': user.has_cnb_permission,
            'web': {
                'viewer': reverse('constitution_bylaws'),
                'pdf': reverse('cnb_document_pdf'),
                'new_resolution': reverse('cnb_create_resolution'),
            },
        })


class CnbSectionHistoryView(MobileAPIView):
    web_page = 'cnb_section_history'
    web_page_toggle = False   # section_history has no @require_page_enabled

    def get(self, request, section_id):
        section = history_section_or_404(section_id, request.user)
        article = section.article
        doc = article.document
        return Response({
            'section': {
                'id': section.id,
                'number': section.number,
                'title': section.title,
                'full_identifier': section.full_identifier,
                'content': section.content,
                'active': section.is_active,
                'removed': bool(section.removed_at),
                'removed_at': section.removed_at.isoformat() if section.removed_at else None,
            },
            'document': {'doc_type': doc.doc_type, 'label': doc.get_doc_type_display(), 'title': doc.title},
            'article': {'number': article.number, 'title': article.title},
            'anchor': section_anchor(section),
            'versions': [{
                'id': v['rev'].id,
                'title': v['rev'].title,
                'content': v['rev'].content,
                'was_active': v['rev'].was_active,
                'replaced_at': v['rev'].replaced_at.isoformat(),
                'source': v['rev'].source,
                'source_label': v['rev'].get_source_display(),
                'resolution': ({'id': v['rev'].resolution_id, 'title': v['rev'].resolution.title}
                               if v['rev'].resolution_id else None),
                'replaced_by': v['rev'].replaced_by.name if v['rev'].replaced_by_id else None,
                'note': v['rev'].note,
                'diff': [{'op': op, 'text': text} for op, text in v['segments']],
            } for v in section_versions(section)],
            'web_path': reverse('cnb_section_history', kwargs={'section_id': section.pk}),
        })


def _resolution_summary(r):
    return {
        'id': r.id,
        'title': r.title,
        'type': r.resolution_type,
        'type_label': r.get_resolution_type_display(),
        'status': r.status,
        'status_label': r.get_status_display(),
        'authors': r.authors,
        'vote_date': _date(r.vote_date),
        'amendments': r.amendment_total,
        'created_at': r.created_at.isoformat(),
    }


class CnbResolutionListView(MobileAPIView):
    web_page = 'constitution_bylaws'
    web_page_toggle = False

    def get(self, request):
        rows = Resolution.objects.annotate(amendment_total=Count('amendments')).order_by('-created_at')
        return Response({
            'resolutions': [_resolution_summary(r) for r in rows],
            'can_create_resolution': request.user.can_create_resolution,
        })


class CnbResolutionView(MobileAPIView):
    web_page = 'cnb_resolution_detail'
    web_page_toggle = False

    def get(self, request, resolution_id):
        r = get_object_or_404(
            Resolution.objects.annotate(amendment_total=Count('amendments'))
            .prefetch_related('amendments__section__article__document'),
            pk=resolution_id,
        )
        can_edit = r.status in ('draft', 'pending') and _can_edit_resolution(request.user, r)
        # 11-04-26: the builder (views_cnb_resolutions.py) — status buttons,
        # structure changes, collaborators and per-amendment extras.
        from src.mobile.views_cnb_resolutions import builder_payload
        builder = builder_payload(request.user, r, can_edit)
        extra = builder.pop('amendment_extra')
        return Response({
            **builder,
            **_resolution_summary(r),
            'sponsors': r.sponsors,
            'whereas': [line.strip() for line in (r.whereas_clauses or '').splitlines() if line.strip()],
            'resolved': r.resolved_clauses,
            'body': r.resolution_body,
            'notes': r.additional_notes,
            'passed_at': r.passed_at.isoformat() if r.passed_at else None,
            'failed_at': r.failed_at.isoformat() if r.failed_at else None,
            'amendment_list': [{
                'id': a.id,
                'section': a.identifier_snapshot or a.section.full_identifier,
                'section_title': a.section.title,
                'type': a.amendment_type,
                'type_label': a.get_amendment_type_display().split(' — ')[0],
                'scope_note': a.scope_note,
                'original': a.original_text_snapshot,
                'proposed': a.proposed_text,
                'removes_section': a.is_whole_section_removal,
                'applied': a.applied,
                **extra.get(a.id, {}),
            } for a in r.amendments.all()],
            'can_edit': can_edit,
            # Batch 6: the working group's sticky notes (views_cnb_manage).
            'can_use_notes': ResolutionNote.user_can_use_notes(request.user, r),
            'web_path': reverse('cnb_resolution_detail', kwargs={'resolution_id': r.pk}),
        })
