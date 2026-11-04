"""
Constitution & Bylaws management and resolution notes in the app (batch 6),
through the same functions as the website's manager (src/view/officer/cnb.py)
and sticky notes (src/view/officer/cnb_notes.py).

The manager (``cnb_required``: admins and the CNB role, as on the website):

* ``GET cnb/manage/`` — every governing document (switched-off ones too, as
  the manager shows them, each with ``enabled``), article by article and
  section by section, with suspensions and partial suspensions.
* ``POST cnb/manage/documents/<doc_type>/articles/`` ``{number, title}`` —
  ``add_article``.
* ``PATCH cnb/manage/articles/<id>/`` ``{title}`` — ``rename_article`` (logged).
* ``POST cnb/manage/articles/<id>/active/`` ``{active, reason}`` —
  ``toggle_article_active``. The app says which state it wants, so a repeated
  tap can't flip it back; a reason is required to suspend.
* ``POST cnb/manage/articles/<id>/sections/`` ``{number, title, content}`` —
  ``add_section``.
* ``PATCH cnb/manage/sections/<id>/`` ``{content, title}`` — ``edit_section``.
* ``POST cnb/manage/sections/<id>/active/`` ``{active, reason}`` —
  ``toggle_section_active``.
* ``POST cnb/manage/sections/<id>/partials/`` ``{ref, reason}`` and
  ``DELETE cnb/manage/sections/<id>/partials/<idx>/`` — partial suspensions.

Text and status changes are recorded as section revisions by the shared
helpers (``Section.record_revision``), so ``cnb/sections/<id>/history/`` and
"as of" show app edits exactly like website edits.

Resolution notes (the working group: CNB holders, the author while a brother,
and collaborators; anyone else gets a 404, as on the website):

* ``GET/POST cnb/resolutions/<id>/notes/`` — the notes (open first, newest
  first) and the choices; ``{body, color, anchor}`` adds one.
* ``PATCH/DELETE cnb/resolutions/<id>/notes/<note_id>/`` — edit (who and when
  recorded) or delete (the note's author, the resolution's author or a CNB
  holder; others are told to mark it done instead).
* ``POST cnb/resolutions/<id>/notes/<note_id>/done/`` — tick off or reopen.

The resolution builder itself (drafting, amendments, structure changes,
status, collaborators) is ``views_cnb_resolutions.py``.
"""
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import Article, GoverningDocument, Resolution, ResolutionNote, Section
from src.models.users import member_defer
from src.view.officer import cnb, cnb_notes


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _text(body, key):
    value = body.get(key)
    return value if isinstance(value, str) else ''


def _refuse(error):
    if error:
        raise BadRequest(error, code='invalid_input')


class _Manager(MobileAPIView):
    """``@cnb_required``: admins and CNB role holders."""
    web_page_toggle = False   # the manager's views have no @require_page_enabled

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.has_cnb_permission:
            raise exceptions.PermissionDenied('Constitution & Bylaws Chair access required.', code='forbidden')

    @staticmethod
    def wanted_state(body):
        active = body.get('active')
        if not isinstance(active, bool):
            raise BadRequest('active is true or false.', code='invalid_input')
        return active


def _who(user):
    return user.get_display_name() if user else None


def _section_json(s):
    return {
        'id': s.id,
        'number': s.number,
        'title': s.title,
        'content': s.content,
        'active': s.is_active,
        'deactivation_reason': s.deactivation_reason,
        'deactivated_at': s.deactivated_at.isoformat() if s.deactivated_at else None,
        'partial_suspensions': [
            {'index': i, 'ref': p.get('ref', ''), 'reason': p.get('reason', ''),
             'suspended_at': p.get('suspended_at', ''), 'suspended_by': p.get('suspended_by_name', '')}
            for i, p in enumerate(s.partial_suspensions or []) if isinstance(p, dict)
        ],
        'protected_until': s.protected_until.isoformat() if s.amendment_protected and s.protected_until else None,
    }


def _article_json(a):
    return {
        'id': a.id,
        'number': a.number,
        'title': a.title,
        'active': a.is_active,
        'deactivation_reason': a.deactivation_reason,
        'sections': [_section_json(s) for s in a.sections.all()],
    }


def _section(section_id):
    """A section in the document (one a resolution struck is a 404, as on the website)."""
    return get_object_or_404(Section.objects.select_related('article__document'), pk=section_id)


class CnbManageView(_Manager):
    web_page = 'cnb_manage_document'

    def get(self, request):
        enabled = set(GoverningDocument.enabled().values_list('pk', flat=True))
        documents = GoverningDocument.objects.prefetch_related('articles__sections')
        return Response({'documents': [{
            'doc_type': d.doc_type,
            'label': d.get_doc_type_display(),
            'title': d.title,
            'enabled': d.pk in enabled,
            'prose_only': d.is_prose_only,
            'articles': [_article_json(a) for a in d.articles.all()],
        } for d in documents]})


class CnbAddArticleView(_Manager):
    web_page = 'cnb_add_article'

    def post(self, request, doc_type):
        document = get_object_or_404(GoverningDocument, doc_type=doc_type)
        body = _body(request)
        article, error = cnb.create_article(document, _text(body, 'number'), _text(body, 'title'))
        _refuse(error)
        return Response({'article': _article_json(article)}, status=201)


class CnbArticleView(_Manager):
    web_page = 'cnb_rename_article'

    def patch(self, request, article_id):
        article = get_object_or_404(Article.objects.select_related('document'), pk=article_id)
        outcome, _ = cnb.retitle_article(request._request, article, _text(_body(request), 'title'))
        if outcome == 'error':
            raise BadRequest('An article needs a title.', code='invalid_input')
        return Response({'article': _article_json(article), 'changed': outcome == 'ok'})


class CnbArticleActiveView(_Manager):
    web_page = 'cnb_toggle_article'

    def post(self, request, article_id):
        article = get_object_or_404(Article, pk=article_id)
        body = _body(request)
        _refuse(cnb.set_article_active(article, self.wanted_state(body), _text(body, 'reason'), request.user))
        return Response({'article': _article_json(article)})


class CnbAddSectionView(_Manager):
    web_page = 'cnb_add_section'

    def post(self, request, article_id):
        article = get_object_or_404(Article, pk=article_id)
        body = _body(request)
        section, error = cnb.create_section(article, _text(body, 'number'), _text(body, 'title'),
                                            _text(body, 'content'))
        _refuse(error)
        return Response({'section': _section_json(section)}, status=201)


class CnbSectionView(_Manager):
    web_page = 'cnb_edit_section'

    def patch(self, request, section_id):
        section = _section(section_id)
        body = _body(request)
        # The website keeps the title when the form leaves it out; so does this.
        title = body['title'] if isinstance(body.get('title'), str) else section.title
        _refuse(cnb.save_section_text(section, _text(body, 'content'), title, request.user))
        return Response({'section': _section_json(section)})


class CnbSectionActiveView(_Manager):
    web_page = 'cnb_toggle_section'

    def post(self, request, section_id):
        section = _section(section_id)
        body = _body(request)
        _refuse(cnb.set_section_active(section, self.wanted_state(body), _text(body, 'reason'), request.user))
        return Response({'section': _section_json(section)})


class CnbPartialSuspensionsView(_Manager):
    web_page = 'cnb_add_partial_suspension'

    def post(self, request, section_id):
        section = _section(section_id)
        body = _body(request)
        _refuse(cnb.add_section_partial(section, _text(body, 'ref'), _text(body, 'reason'), request.user))
        return Response({'section': _section_json(section)}, status=201)


class CnbPartialSuspensionView(_Manager):
    web_page = 'cnb_remove_partial_suspension'

    def delete(self, request, section_id, idx):
        section = _section(section_id)
        if cnb.remove_section_partial(section, idx) is None:
            raise Http404
        return Response({'section': _section_json(section)})


# ── Resolution notes ─────────────────────────────────────────────────────────

def _note_json(n, user):
    return {
        'id': n.id,
        'body': n.body,
        'color': n.color,
        'anchor': n.anchor,
        'anchor_label': n.get_anchor_display() if n.anchor else '',
        'created_by': _who(n.created_by),
        'created_at': n.created_at.isoformat(),
        'edited_by': _who(n.edited_by),
        'edited_at': n.edited_at.isoformat() if n.edited_at else None,
        'is_done': n.is_done,
        'done_by': _who(n.done_by),
        'done_at': n.done_at.isoformat() if n.done_at else None,
        'can_delete': n.user_can_delete(user),
    }


class _Notes(MobileAPIView):
    web_page = 'cnb_resolution_detail'
    web_page_toggle = False   # resolution_detail has no @require_page_enabled

    def resolution(self, request, resolution_id):
        resolution = get_object_or_404(Resolution, pk=resolution_id)
        if not ResolutionNote.user_can_use_notes(request.user, resolution):
            raise Http404   # as on the website: outsiders can't tell notes exist
        return resolution

    def note(self, request, resolution_id, note_id):
        resolution = self.resolution(request, resolution_id)
        return get_object_or_404(ResolutionNote.objects.select_related('resolution'), pk=note_id,
                                 resolution=resolution)

    @staticmethod
    def payload(note, user):
        return Response({'note': _note_json(
            ResolutionNote.objects.select_related('created_by', 'edited_by', 'done_by', 'resolution')
            .defer(*member_defer('created_by', 'edited_by', 'done_by')).get(pk=note.pk), user)})


class ResolutionNotesView(_Notes):

    def get(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        notes = (resolution.notes.select_related('created_by', 'edited_by', 'done_by', 'resolution')
                 .defer(*member_defer('created_by', 'edited_by', 'done_by')))
        return Response({
            'notes': [_note_json(n, request.user) for n in notes],
            'colors': [{'value': v, 'label': str(label)} for v, label in ResolutionNote.COLOR_CHOICES],
            'anchors': [{'value': v, 'label': str(label)} for v, label in ResolutionNote.ANCHOR_CHOICES],
            'max_length': ResolutionNote.MAX_LENGTH,
        })

    def post(self, request, resolution_id):
        resolution = self.resolution(request, resolution_id)
        data = _body(request)
        body, error = cnb_notes.clean_body(_text(data, 'body'))
        _refuse(error)
        note = cnb_notes.create_note(resolution, request.user, body,
                                     cnb_notes.clean_color(data.get('color', 'yellow')),
                                     cnb_notes.clean_anchor(data.get('anchor', '')))
        response = self.payload(note, request.user)
        response.status_code = 201
        return response


class ResolutionNoteView(_Notes):

    def patch(self, request, resolution_id, note_id):
        note = self.note(request, resolution_id, note_id)
        data = _body(request)
        body, error = cnb_notes.clean_body(_text(data, 'body') if 'body' in data else note.body)
        _refuse(error)
        cnb_notes.update_note(note, request.user, body,
                              cnb_notes.clean_color(data.get('color', note.color), default=note.color),
                              cnb_notes.clean_anchor(data.get('anchor'), default=note.anchor))
        return self.payload(note, request.user)

    def delete(self, request, resolution_id, note_id):
        note = self.note(request, resolution_id, note_id)
        error = cnb_notes.delete_note(note, request.user)
        if error:
            raise exceptions.PermissionDenied(error, code='forbidden')
        return Response({'ok': True})


class ResolutionNoteDoneView(_Notes):

    def post(self, request, resolution_id, note_id):
        note = self.note(request, resolution_id, note_id)
        cnb_notes.toggle_note_done(note, request.user)
        return self.payload(note, request.user)
