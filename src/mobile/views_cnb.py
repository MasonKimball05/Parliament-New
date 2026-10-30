"""
The app's Constitution & Bylaws reader (mobile plan, Phase 3g).

* ``GET cnb/`` — every governing document members can see
  (``GoverningDocument.enabled()``), article by article, section by section,
  with the same suspension, partial-suspension and amendment-protection notes
  as the Document tab of ``cnb_viewer``. Each section carries the spans of its
  cross-references that resolve (``src/cnb_crossrefs.py``) so the app can link
  them, exactly as the website's ``cnb_linked`` tag does.
* ``GET cnb/resolutions/`` — the Resolutions tab: every resolution, newest first.
* ``GET cnb/resolutions/<id>/`` — one resolution, read-only
  (``resolution_detail``): its clauses, body, notes and amendments.

Writing and managing stay on the website (the app links there): drafting a
resolution, editing it, the officer manager, section history and the PDF.
"""
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.response import Response

from src.cnb_crossrefs import Structure, anchor_for, find_references
from src.mobile.api import MobileAPIView
from src.models import GoverningDocument, Resolution
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
    }


class CnbDocumentsView(MobileAPIView):
    web_page = 'constitution_bylaws'
    web_page_toggle = False   # cnb_viewer has no @require_page_enabled

    def get(self, request):
        documents = list(GoverningDocument.enabled().prefetch_related('articles__sections'))
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
                    'sections': [_section_json(s, doc.doc_type, structure) for s in a.sections.all()],
                } for a in articles],
            })
        user = request.user
        return Response({
            'documents': out,
            'can_create_resolution': user.can_create_resolution,
            'can_manage': user.has_cnb_permission,
            'web': {
                'viewer': reverse('constitution_bylaws'),
                'pdf': reverse('cnb_document_pdf'),
                'new_resolution': reverse('cnb_create_resolution'),
            },
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
        return Response({
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
            } for a in r.amendments.all()],
            'can_edit': can_edit,
            'web_path': reverse('cnb_resolution_detail', kwargs={'resolution_id': r.pk}),
        })
