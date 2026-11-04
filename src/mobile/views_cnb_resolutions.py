"""
The Constitution & Bylaws resolution builder in the app (11-04-26), through
the same functions as the website's builder (``src/view/officer/cnb.py``), so
who may do what, the validation, the status rules and the live-editing
broadcasts are the same on both.

Writing a resolution (``can_create_resolution``: brothers, not pledges or
advisors):

* ``GET / POST cnb/resolutions/create/`` — ``create_resolution``: the type
  choices, then ``{title, resolution_type, authors, sponsors, vote_date,
  whereas_clauses, resolved_text, resolution_body, additional_notes}``.

Changing one (``_can_edit_resolution``: the C&B Chair and admins, the author
while a brother, and Editor collaborators; only while it is draft or pending):

* ``GET / PATCH cnb/resolutions/<id>/edit/`` — ``edit_resolution``. PATCH
  ``{fields: {<field>: value}, original: {<field>: value when loaded}}``
  writes only the fields sent. A field someone else changed since it was
  loaded is not written: 409 ``conflict`` with ``conflicts: [{field, label,
  theirs, mine}]`` (the other fields are saved, as on the website). Every
  field is checked before any is written, so a refused one is a 400 that
  saves nothing.
* ``GET / POST cnb/resolutions/<id>/amendments/`` — the amendment picker (the
  documents members can see, article by article, section by section) and
  ``add_amendment`` ``{section_id, proposed_text, scope_note, section_title}``
  (adds, or replaces this resolution's amendment to that section).
* ``GET cnb/resolutions/<id>/sections/<section_id>/`` — ``section_context_api``
  for this resolution: the section's current text and its neighbours', this
  resolution's amendment to it and the title it proposes, and who has it open
  in the website's editor.
* ``DELETE cnb/resolutions/<id>/amendments/<amendment_id>/`` — ``remove_amendment``.
* ``GET / POST cnb/resolutions/<id>/structure/`` — the article / section maker
  and ``add_structure_change`` (``change_id`` edits a proposal in place).
* ``DELETE cnb/resolutions/<id>/structure/<change_id>/`` — ``remove_structure_change``.

Running one (``_can_manage_resolution``: the chair, and the author while a
brother):

* ``POST cnb/resolutions/<id>/status/`` ``{status}`` — ``set_resolution_status``:
  the same transitions; Passed and Failed are the chair's.
* ``GET / POST cnb/resolutions/<id>/collaborators/`` — the collaborators, the
  members who may be added, and ``add_collaborator`` ``{user_id, role}``
  (changes the role of someone already there).
* ``DELETE cnb/resolutions/<id>/collaborators/<collaborator_id>/`` — ``remove_collaborator``.

What the resolution page shows about these (status buttons, structure
changes, collaborators) is in ``cnb/resolutions/<id>/`` (``views_cnb.py``,
built by ``builder_payload`` here). The print view and the "how the
documents would read" preview stay on the website.
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src import cnb_live, cnb_structure
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import (
    GoverningDocument, Resolution, ResolutionAmendment, ResolutionCollaborator, ResolutionStructureChange, Section,
)
from src.view.officer import cnb


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _form(body, *names):
    """
    The fields sent, as the website's form would post them: strings ('' for
    anything else). A field not sent is left out, so the helpers' defaults
    for a missing field apply as they do on the website.
    """
    out = {}
    for name in names:
        if name not in body:
            continue
        value = body.get(name)
        if isinstance(value, bool):
            value = ''
        elif isinstance(value, int):
            value = str(value)
        out[name] = value if isinstance(value, str) else ''
    return out


def _refuse(error):
    if error:
        raise BadRequest(error, code='invalid_input')


def _forbid(message):
    raise exceptions.PermissionDenied(message, code='forbidden')


def _refuse_change(refusal):
    """`resolution_change_refusal`'s answer: not allowed is a 403, a closed resolution a 400."""
    if refusal == cnb.EDIT_DENIED:
        _forbid(refusal)
    _refuse(refusal)


def _date(d):
    return d.isoformat() if d else None


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


class _Builder(MobileAPIView):
    """A resolution the caller may change now (``_edit_denied`` + the open-status check)."""
    web_page_toggle = False   # the builder's web views have no @require_page_enabled
    closed_message = cnb.CLOSED

    def resolution(self, request, resolution_id):
        resolution = get_object_or_404(Resolution, pk=resolution_id)
        _refuse_change(cnb.resolution_change_refusal(request.user, resolution, self.closed_message))
        return resolution


class _Manager(MobileAPIView):
    """A resolution the caller runs (``_can_manage_resolution``)."""
    web_page_toggle = False

    def resolution(self, request, resolution_id):
        resolution = get_object_or_404(Resolution, pk=resolution_id)
        if not cnb._can_manage_resolution(request.user, resolution):
            _forbid(cnb.COLLABORATORS_DENIED)
        return resolution


# ── What the resolution page shows (used by views_cnb.CnbResolutionView) ──────

#: The detail page's status buttons: label and the confirmation it asks for.
STATUS_BUTTONS = {
    'pending': ('Move to Pending Vote', ''),
    'withdrawn': ('Withdraw', 'Withdraw this resolution?'),
    'passed': ('Resolution Passed', 'Mark as PASSED? This will apply all tracked amendments to the live document.'),
    'failed': ('Resolution Failed', 'Mark as FAILED? Impacted sections will be protected from new amendments.'),
    'draft': ('Return to Draft', ''),
}


def _structure_rows(resolution):
    return [{
        'id': row['change'].id,
        'kind': row['change'].kind,
        'kind_label': row['change'].get_kind_display(),
        'heading': row['says']['heading'],
        'detail': row['says']['detail'],
        'renumbering': row['says']['renumbering'],
        'error': row['says']['error'],
        'applied': row['change'].applied,
        'title': row['change'].title,
        'content': row['change'].content,
        'document_id': row['change'].document_id,
        'article_id': row['change'].article_id,
        'parent_id': row['change'].parent_id,
        'section_id': row['change'].section_id,
        'before_article_id': row['change'].before_article_id,
        'before_section_id': row['change'].before_section_id,
        'refs': [{'source': ref['source'], 'text': ref['text']} for ref in row['refs']],
    } for row in cnb._structure_context(resolution)['structure_rows']]


def _collaborator_json(c):
    return {'id': c.id, 'user_id': c.user_id, 'name': c.user.name, 'role': c.role, 'role_label': c.get_role_display()}


def builder_payload(user, resolution, can_edit):
    """
    The builder's parts of ``resolution_detail``: the Actions card's status
    buttons, the structure changes, the collaborators, and what to show for
    each amendment beyond the text (keyed by amendment id).
    """
    can_manage = cnb._can_manage_resolution(user, resolution)
    actions = cnb.status_actions(user, resolution)
    pending_titles = resolution.pending_section_titles
    return {
        'can_manage': can_manage,
        'is_cnb': user.has_cnb_permission,
        'status_actions': [
            {'status': s, 'label': STATUS_BUTTONS[s][0], 'confirm': STATUS_BUTTONS[s][1]} for s in actions
        ],
        # The detail page's line for an author whose resolution is pending.
        'status_note': ('The Constitution & Bylaws Chair records whether it passed or failed.'
                        if can_manage and can_edit and resolution.status == 'pending'
                        and not user.has_cnb_permission else ''),
        'structure_changes': _structure_rows(resolution),
        'collaborators': [_collaborator_json(c) for c in resolution.collaborators.select_related('user')],
        'amendment_extra': {
            a.id: {
                'section_id': a.section_id,
                'proposed_title': pending_titles.get(a.section_id, ''),
                'renumbering_note': a.renumbering_note,
                'can_remove': can_edit and not a.applied,
            } for a in resolution.amendments.all()
        },
        'web': {
            'print': reverse('cnb_resolution_print', kwargs={'resolution_id': resolution.pk}),
            'preview': (reverse('cnb_resolution_document_preview', kwargs={'resolution_id': resolution.pk})
                        if resolution.status in cnb.OPEN_STATUSES else None),
        },
    }


# ── Writing ──────────────────────────────────────────────────────────────────

_FIELDS = cnb_live.FIELDS


class CnbResolutionCreateView(MobileAPIView):
    web_page = 'cnb_create_resolution'
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.can_create_resolution:
            _forbid(cnb.CREATE_DENIED)

    def get(self, request):
        return Response({'types': _choices(Resolution.TYPE_CHOICES), 'labels': cnb_live.FIELD_LABELS})

    def post(self, request):
        resolution, error = cnb.create_resolution_record(request.user, _form(_body(request), *_FIELDS))
        _refuse(error)
        return Response({'id': resolution.pk, 'message': f'Resolution "{resolution.title}" created.'}, status=201)


class CnbResolutionEditView(_Builder):
    web_page = 'cnb_edit_resolution'
    closed_message = cnb.EDIT_CLOSED

    def get(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        return Response({
            'id': resolution.pk,
            'status': resolution.status,
            'fields': cnb_live.baseline(resolution),
            'types': _choices(Resolution.TYPE_CHOICES),
            'labels': cnb_live.FIELD_LABELS,
        })

    def patch(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        body = _body(request)
        raw_fields = body.get('fields') if isinstance(body.get('fields'), dict) else {}
        raw_origs = body.get('original') if isinstance(body.get('original'), dict) else {}
        values = _form(raw_fields, *_FIELDS)
        origs = _form(raw_origs, *_FIELDS)
        # Every field is checked first: the website saves the good fields and
        # flashes the bad ones, which a form in the app can't show the same way.
        for field, value in values.items():
            new = value if field == 'resolution_type' else cnb_live.norm(value)
            _refuse(cnb.resolution_field_problem(field, new))
        live, written, conflicts, errors = cnb.save_resolution_fields(
            resolution.pk, values, origs, request.user, check_conflicts=True)
        _refuse(errors[0] if errors else None)
        payload = {'saved': written, 'fields': cnb_live.baseline(live)}
        if conflicts:
            return Response({
                'error': 'conflict',
                'detail': cnb.conflict_message(conflicts, written),
                'conflicts': conflicts,
                **payload,
            }, status=409)
        return Response({**payload, 'message': 'Resolution updated.' if written else 'No changes to save.'})


# ── Amendments ───────────────────────────────────────────────────────────────

def _ordered(items):
    return sorted(items, key=lambda x: (x.display_order, x.pk))


class CnbResolutionAmendmentsView(_Builder):
    web_page = 'cnb_add_amendment'

    def get(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        amended = set(resolution.amendments.values_list('section_id', flat=True))
        pending_titles = resolution.pending_section_titles
        # The amendment popup's section list: the documents members can see,
        # for the chair too (resolution_detail.html's `documents`).
        documents = GoverningDocument.enabled().prefetch_related('articles__sections')
        return Response({'documents': [{
            'id': d.pk,
            'doc_type': d.doc_type,
            'label': d.get_doc_type_display(),
            'articles': [{
                'id': a.pk,
                'number': a.number,
                'title': a.title,
                'sections': [{
                    'id': s.pk,
                    'number': s.number,
                    'title': s.title,
                    'identifier': f'{d.get_doc_type_display()} Art. {a.number} § {s.number}',
                    'protected_until': _date(s.protected_until) if s.amendment_protected else None,
                    'protected': s.amendment_protected,
                    'active': s.is_active,
                    'amended': s.pk in amended,
                    'proposed_title': pending_titles.get(s.pk, ''),
                } for s in _ordered(a.sections.all())],
            } for a in _ordered(d.articles.all())],
        } for d in documents]})

    def post(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        body = _body(request)
        form = _form(body, 'section_id', 'proposed_text', 'scope_note', 'section_title')
        # The website's popup always posts the title box once a section is
        # chosen; leaving it out of the request leaves the title alone.
        title = form['section_title'] if isinstance(body.get('section_title'), str) else None
        section_id = form.get('section_id', '')
        if section_id and not section_id.isdigit():
            raise BadRequest('A section must be selected.', code='invalid_input')
        message, error = cnb.save_amendment(request.user, resolution, section_id, form.get('proposed_text', ''),
                                            form.get('scope_note', ''), title)
        _refuse(error)
        return Response({'message': message})


class CnbResolutionAmendmentView(_Builder):
    web_page = 'cnb_remove_amendment'

    def delete(self, request, resolution_id, amendment_id):
        resolution = get_object_or_404(Resolution, pk=resolution_id)
        if not cnb._can_edit_resolution(request.user, resolution):
            _forbid(cnb.EDIT_DENIED)
        amendment = get_object_or_404(ResolutionAmendment, pk=amendment_id, resolution=resolution)
        _refuse_change(cnb.resolution_change_refusal(request.user, resolution))
        return Response({'message': cnb.delete_amendment(request.user, resolution, amendment)})


class CnbResolutionSectionView(_Builder):
    """The amendment editor's section (``section_context_api``), for one resolution."""
    web_page = 'cnb_section_api'

    def get(self, request, resolution_id, section_id):
        resolution = self.resolution(request, resolution_id)
        section = get_object_or_404(Section.objects.select_related('article__document'), pk=section_id)
        # As `add_amendment`: the chair any document, anyone else those members can see.
        if not cnb.amendable_documents(request.user).filter(pk=section.article.document_id).exists():
            raise exceptions.NotFound()
        amendment = ResolutionAmendment.objects.filter(resolution=resolution, section=section).first()
        lock = cnb_live.current_locks(resolution.pk).get(f'amend:{section.pk}')
        return Response({
            **cnb.section_context(section),
            'protection_note': section.protection_note if section.amendment_protected else '',
            'proposed_title': resolution.pending_section_titles.get(section.pk, ''),
            'amendment': {
                'id': amendment.id,
                'proposed_text': amendment.proposed_text,
                'scope_note': amendment.scope_note,
                'type': amendment.amendment_type,
                'removes_section': amendment.is_whole_section_removal,
            } if amendment else None,
            'open_on_website_by': lock.get('name') if isinstance(lock, dict) else None,
        })


# ── Structure changes ────────────────────────────────────────────────────────

class CnbResolutionStructureView(_Builder):
    web_page = 'cnb_add_structure_change'

    def get(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        # The maker's dropdowns (resolution_detail's `documents`: the ones members can see).
        documents = GoverningDocument.enabled().prefetch_related('articles__sections')
        context = cnb._structure_context(resolution, documents)
        return Response({
            'documents': context['structure_payload'],
            'new_articles': context['structure_new_articles'],
            'kinds': _choices(ResolutionStructureChange.KIND_CHOICES),
            'changes': _structure_rows(resolution),
        })

    def post(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        form = _form(_body(request), 'kind', 'title', 'content', 'change_id', 'document_id', 'before_article_id',
                     'parent_id', 'article_id', 'before_section_id', 'section_id')
        message, error = cnb.save_structure_change(request.user, resolution, form)
        _refuse(error)
        return Response({'message': message})


class CnbResolutionStructureChangeView(_Builder):
    web_page = 'cnb_remove_structure_change'

    def delete(self, request, resolution_id, change_id):
        resolution = get_object_or_404(Resolution, pk=resolution_id)
        if not cnb._can_edit_resolution(request.user, resolution):
            _forbid(cnb.EDIT_DENIED)
        change = get_object_or_404(ResolutionStructureChange, pk=change_id, resolution=resolution)
        message, error = cnb.delete_structure_change(request.user, resolution, change)
        _refuse(error)
        return Response({'message': message})


# ── Status and collaborators ─────────────────────────────────────────────────

class CnbResolutionStatusView(MobileAPIView):
    web_page = 'cnb_set_status'
    web_page_toggle = False

    def post(self, request, resolution_id):
        status = _body(request).get('status')
        level, message = cnb.change_resolution_status(
            request.user, resolution_id, status if isinstance(status, str) else '')
        if level == 'error':
            # Who may (the chair; the author but not for the vote result) is a
            # 403, a status it can't go to from here a 400.
            if message.startswith('Only the Constitution & Bylaws Chair'):
                _forbid(message)
            raise BadRequest(message, code='invalid_input')
        resolution = Resolution.objects.get(pk=resolution_id)
        return Response({
            'status': resolution.status,
            'status_label': resolution.get_status_display(),
            'level': level,
            'message': message or f'Resolution moved to {resolution.get_status_display().lower()}.',
        })


class CnbResolutionCollaboratorsView(_Manager):
    web_page = 'cnb_add_collaborator'

    def get(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        pool = cnb.collaborator_pool(request.user).exclude(pk=request.user.pk).order_by('name')
        return Response({
            'collaborators': [_collaborator_json(c) for c in resolution.collaborators.select_related('user')],
            'members': [{'user_id': u.pk, 'name': u.name} for u in pool.only('user_id', 'name')],
            'roles': [
                {'value': 'viewer', 'label': 'Viewer', 'detail': 'Can view this resolution (no edit access)'},
                {'value': 'editor', 'label': 'Editor', 'detail': 'Can change the text and amendments'},
            ],
        })

    def post(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        form = _form(_body(request), 'user_id', 'role')
        message, error = cnb.save_collaborator(request.user, resolution, form.get('user_id', ''),
                                               form.get('role', 'viewer'))
        _refuse(error)
        return Response({'message': message})


class CnbResolutionCollaboratorView(_Manager):
    web_page = 'cnb_remove_collaborator'

    def delete(self, request, resolution_id, collaborator_id):
        resolution = self.resolution(request, resolution_id)
        collab = get_object_or_404(ResolutionCollaborator.objects.select_related('user'), pk=collaborator_id,
                                   resolution=resolution)
        return Response({'message': cnb.delete_collaborator(collab)})

