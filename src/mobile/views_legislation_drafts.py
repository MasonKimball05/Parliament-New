"""
Legislation drafts in the app (v4.4.x): the website's My Work "Drafts" tab and
its draft editor (src/view/legislation_drafts.py), with the same form
(``LegislationDraftForm``), the same ownership-scoped read (``_get_own_draft``)
and the same publish (``publish_draft``, shared with the web view), so a draft
saved or published from the app is the one the website would have made.

* ``GET legislation/drafts/form/`` — what the editor offers: the vote modes and
  thresholds (the form's choices), the new draft's defaults, the help texts and
  whether the member ``can_publish``.
* ``POST legislation/drafts/`` — a new draft (JSON). Fields: ``title``,
  ``description``, ``notes``, ``planned_available_at`` and
  ``planned_voting_ends_at`` (ISO 8601 or null), ``vote_mode``,
  ``required_percentage``, ``anonymous_vote``, ``allow_abstain``. 201
  ``{draft, message}``; 400 ``invalid_input`` with the form's errors.
* ``GET legislation/drafts/<id>/`` — one of the member's own drafts (published
  ones too, read-only: the private notes stay theirs), with
  ``ready_to_publish`` and ``not_ready_reason``, ``can_publish``, and the
  attachment's site path when it has one. ``POST`` — the edited draft, the
  same fields as creating one; a field left out keeps its saved value. 200
  ``{draft, message}``; 400 ``invalid_input``; 400 ``already_published``.
* ``POST legislation/drafts/<id>/delete/`` — delete it. 200 ``{ok, message}``.
* ``POST legislation/drafts/<id>/publish/`` — publish it. Officers and chairs
  only (the web's ``@officer_required``, through the same ``_can_publish``):
  403 ``forbidden`` for anyone else, before the draft is even looked up, as
  the decorator runs before the view. 400 ``not_ready`` with the reason. 200
  ``{draft, bill: {id, title}, announced, message}``.

Every read is ``_get_own_draft``, so another member's draft is a 404 and never
a 403: for a private document the existence is part of what is private.

The app sends text fields only. The optional document stays on the website
(the app has no document picker): the app's form leaves the field out
altogether, so an edit from the app can neither replace nor clear the file,
and a draft that has one reports its site path (the author-only
``legislation_draft_document`` route) for the app to open.
"""
from django.forms.models import model_to_dict
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from src.forms import LegislationDraftForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import LegislationDraft
from src.view.legislation_drafts import _can_publish, _get_own_draft, publish_draft, publish_message

#: The form's fields the app edits: all of them but the document.
APP_FIELDS = [f for f in LegislationDraftForm.Meta.fields if f != 'document']
BOOLEAN_FIELDS = ('anonymous_vote', 'allow_abstain')
DATE_FIELDS = ('planned_available_at', 'planned_voting_ends_at')


class AppDraftForm(LegislationDraftForm):
    """``LegislationDraftForm`` without ``document``: the app cannot touch the file."""

    class Meta(LegislationDraftForm.Meta):
        fields = APP_FIELDS


def _iso(dt):
    return dt.isoformat() if dt else None


def _form_errors(form):
    lines = []
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else None
        lines.extend(f'{label}: {e}' if label else str(e) for e in errors)
    return ' '.join(lines) or 'Check the draft and try again.'


def _form_data(request, draft=None):
    """The JSON body as the form's data; on an edit, a missing field keeps its value."""
    body = request.data if hasattr(request.data, 'get') else {}
    data = {}
    saved = model_to_dict(draft, fields=APP_FIELDS) if draft else {}
    for name in APP_FIELDS:
        value = body.get(name, saved.get(name))
        if name in BOOLEAN_FIELDS:
            # CheckboxInput reads an absent key as False; send a string it parses.
            data[name] = 'true' if value is True or str(value).lower() in ('true', 'on', '1') else 'false'
        elif value is None:
            data[name] = ''
        elif name in DATE_FIELDS and hasattr(value, 'isoformat'):
            data[name] = value.isoformat()
        else:
            data[name] = str(value)
    return data


def _draft_json(draft, user):
    ready, reason = draft.ready_to_publish()
    bill = draft.published_legislation
    return {
        'id': draft.id,
        'title': draft.title,
        'description': draft.description,
        'notes': draft.notes,
        'planned_available_at': _iso(draft.planned_available_at),
        'planned_voting_ends_at': _iso(draft.planned_voting_ends_at),
        'vote_mode': draft.vote_mode,
        'required_percentage': draft.required_percentage,
        'anonymous_vote': draft.anonymous_vote,
        'allow_abstain': draft.allow_abstain,
        'published': draft.is_published,
        'published_at': _iso(draft.published_at),
        'created_at': _iso(draft.created_at),
        'updated_at': _iso(draft.updated_at),
        'bill': {'id': bill.id, 'title': bill.title} if bill else None,
        'document': ({'name': draft.document_display_name or 'Document attached',
                      'path': reverse('legislation_draft_document', args=[draft.id])}
                     if draft.document else None),
        'ready_to_publish': ready,
        'not_ready_reason': '' if ready else reason,
        'can_publish': _can_publish(user),
        'links': {
            'edit': None if draft.is_published else reverse('edit_legislation_draft', args=[draft.id]),
            'bill': reverse('legislation_detail', args=[bill.id]) if bill else None,
        },
    }


def _choices(field):
    return [{'value': str(v), 'label': str(label)} for v, label in field.choices if v != '']


class _Drafts(MobileAPIView):
    web_page_toggle = False   # the web views have no @require_page_enabled
    parser_classes = [JSONParser]


class LegislationDraftFormView(_Drafts):
    web_page = 'create_legislation_draft'

    def get(self, request):
        form = AppDraftForm()
        initial = LegislationDraft()
        return Response({
            'vote_modes': _choices(form.fields['vote_mode']),
            'thresholds': _choices(form.fields['required_percentage']),
            'defaults': {
                'vote_mode': initial.vote_mode,
                'required_percentage': initial.required_percentage,
                'anonymous_vote': initial.anonymous_vote,
                'allow_abstain': initial.allow_abstain,
            },
            'help': {name: str(form.fields[name].help_text or '') for name in APP_FIELDS},
            'can_publish': _can_publish(request.user),
        })


class LegislationDraftsView(_Drafts):
    web_page = 'create_legislation_draft'

    def post(self, request):
        form = AppDraftForm(_form_data(request))
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        draft = form.save(commit=False)
        draft.author = request.user
        draft.save()
        return Response({
            'draft': _draft_json(draft, request.user),
            'message': f'Draft "{draft.title}" saved. Only you can see it until it is published.',
        }, status=201)


class LegislationDraftView(_Drafts):
    web_page = 'edit_legislation_draft'

    def get(self, request, draft_id):
        return Response(_draft_json(_get_own_draft(request, draft_id), request.user))

    def post(self, request, draft_id):
        draft = _get_own_draft(request, draft_id)
        if draft.is_published:
            raise BadRequest('This draft has already been published. Edit the bill itself instead.',
                             code='already_published')
        form = AppDraftForm(_form_data(request, draft), instance=draft)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        draft = form.save()
        return Response({'draft': _draft_json(draft, request.user), 'message': 'Draft updated.'})


class LegislationDraftDeleteView(_Drafts):
    web_page = 'delete_legislation_draft'

    def post(self, request, draft_id):
        draft = _get_own_draft(request, draft_id)
        title = draft.title
        draft.delete()
        return Response({'ok': True, 'message': f'Draft "{title}" deleted.'})


class LegislationDraftPublishView(_Drafts):
    web_page = 'publish_legislation_draft'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # `@officer_required` runs before the web view loads anything, so a
        # non-officer is refused here without learning whether the draft exists.
        if not _can_publish(request.user):
            raise exceptions.PermissionDenied(
                'Publishing a bill to the chapter is done by an officer or chair. '
                'Save your draft and ask one of them to present it.', code='forbidden')

    def post(self, request, draft_id):
        draft = _get_own_draft(request, draft_id)
        ready, reason = draft.ready_to_publish()
        if not ready:
            raise BadRequest(reason, code='not_ready')
        legislation, announced = publish_draft(draft, request.user)
        draft.refresh_from_db()
        return Response({
            'draft': _draft_json(draft, request.user),
            'bill': {'id': legislation.id, 'title': legislation.title},
            'announced': announced,
            'message': publish_message(legislation, announced),
        })
