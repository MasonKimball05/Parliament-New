"""
The three form builders in the app (11-06-26): the Kai chair's report and
commendation forms (``kai_form_builder`` / ``kai_commendation_form_builder``),
the VPP's service hours form (``service_form_builder``) and a slating
period's application form (``slating_form_builder``).

One set of endpoints per builder, the same shape for all three:

* ``GET  <base>`` — the form: its active fields in the page's order, the
  removed ones it offers to restore, the field types its Add Field menu
  offers, which of them take options, and the page's switches (placeholder,
  "show in review", default fields, the field-name pattern, a warning).
  ``POST <base>`` ``{field_type, label, field_name, section, placeholder,
  help_text, options: [..], is_required, show_in_review}`` — add one. 201.
* ``POST <base>reorder/`` ``{order: [field ids]}`` — the drag-and-drop order
  (every id must be one of this form's fields).
* ``GET  <base>fields/<id>/`` — one field (the edit modal's details).
  ``POST`` the same body — save it (the internal name and type never change;
  a Kai built-in field keeps its label).
* ``POST <base>fields/<id>/remove/`` — the page's Delete (the field goes
  inactive; 400 ``builtin`` for a built-in field).
* ``POST <base>fields/<id>/toggle/`` — the page's Restore (Kai: a built-in
  field only comes back on, 400 ``builtin`` otherwise).
* slating only: ``POST <base>defaults/`` — "Add default fields".

``<base>`` is ``kai/form-builder/<discipline|commendation>/``,
``officer/service-hours/form-builder/`` or
``slating/periods/<id>/form-builder/``. Every write answers the form again
with ``message`` (what the page would flash) and ``level``.

Permissions are the pages' own: ``kai_chair_required`` (a Kai chair or a site
admin; a site admin alone when there's no Kai committee), ``vpp_required``
(``user_is_vpp``), ``slating_chair_required`` (``can_manage_period``: the
period's managers, a site admin only while the period isn't locked). None of
the pages has a page toggle or a feature flag; each page's pledge restriction
applies. A slating form outside the setup stage carries the page's warning,
and stays editable, as on the website.

Every write goes through the shared field editing the pages call
(src/view/form_field_builder.py), so names, options, file settings, the
activity log and the refusals are the website's. The JSON is checked first
for what the page's own form enforces in the browser (required boxes, the
name pattern, the menu's types, the inputs' lengths).
"""
import re
from dataclasses import dataclass, field as dc_field
from typing import Callable

from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from src.mobile.api import MobileAPIView, check_web_page
from src.mobile.views_excuses_polls import BadRequest
from src.models import KaiFormField, ServiceFormField, SlatingPeriod
from src.permissions import user_is_vpp
from src.view import kai_form_builder as kai_web
from src.view import service_form_builder as service_web
from src.view.form_field_builder import (
    FieldBuilder, FieldBuilderError, add_field, apply_field_order, normalize_field_name, option_labels,
    remove_field, toggle_field, update_field,
)
# (``src.view.slating`` exports the page's view under the module's name, so import the names.)
from src.view.slating.form_builder import (
    NOT_SETUP_WARNING, SLATING_BUILDER_TYPES, SLATING_FIELDS, SLATING_OPTION_TYPES, add_default_fields,
    slating_builder_fields,
)
from src.view.slating.permissions import can_manage_period

#: The model's own column lengths.
LABEL_MAX = 200
NAME_MAX = 100
PLACEHOLDER_MAX = 200
SECTION_MAX = 100
#: The help text box has no limit on the page; a paragraph is plenty.
HELP_MAX = 2000
#: Choices per field, and each one's length.
OPTIONS_MAX = 100
OPTION_MAX = 200
#: Ids one reorder may name (a form has a few dozen fields).
ORDER_MAX = 500

KAI_FORMS = (('discipline', 'Discipline report form'), ('commendation', 'Commendation form'))


@dataclass
class _Form:
    """One builder's form, for one request: where its rows are and what its page offers."""
    kind: str
    builder: FieldBuilder
    #: Every field of this form (lookups and reorders stay inside it).
    rows: object
    #: ``() -> (active, inactive)`` in the page's order.
    listing: Callable
    types: list
    option_types: tuple
    name_pattern: str
    title: str
    web: str
    scope: dict = dc_field(default_factory=dict)
    unique_scope: dict = dc_field(default_factory=dict)
    placeholder: bool = True
    defaults: bool = False
    warning: str = None
    #: The slating period, for its "Add default fields".
    period: object = None
    extra: dict = dc_field(default_factory=dict)


# ── The three forms ─────────────────────────────────────────────────────────

def _kai_form(request, form_type):
    if form_type not in dict(KAI_FORMS):
        raise exceptions.NotFound('No such Kai form.')
    url_name = kai_web._builder_url_name(form_type)
    if url_name != 'kai_form_builder':
        check_web_page(request.user, url_name, toggle=False)
    if not kai_web.kai_builder_allowed(request.user):
        raise exceptions.PermissionDenied('Only Kai chairs can access this page.', code='forbidden')
    return _Form(
        kind='kai',
        builder=kai_web.KAI_FIELDS,
        rows=KaiFormField.objects.filter(form_type=form_type),
        listing=lambda: kai_web.kai_builder_fields(form_type),
        types=list(KaiFormField.FIELD_TYPES),
        option_types=kai_web.KAI_OPTION_TYPES,
        name_pattern=r'^[a-z_0-9]+$',
        title=dict(KAI_FORMS)[form_type],
        web=reverse(url_name),
        scope={'form_type': form_type},
        extra={'form_type': form_type, 'forms': [{'value': v, 'label': lbl} for v, lbl in KAI_FORMS]},
    )


def _service_form(request):
    if not user_is_vpp(request.user):
        raise exceptions.PermissionDenied(
            'Only the Vice President of Programming can access this page.', code='forbidden')
    service_web.ensure_builtin_fields()   # as the page does on every load
    return _Form(
        kind='service',
        builder=service_web.SERVICE_FIELDS,
        rows=ServiceFormField.objects.all(),
        listing=service_web.service_builder_fields,
        types=list(ServiceFormField.FIELD_TYPES),
        option_types=service_web.SERVICE_OPTION_TYPES,
        name_pattern=r'^[a-z_0-9]+$',
        title='Service hours form',
        web=reverse('service_form_builder'),
    )


def _slating_form(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if not can_manage_period(request.user, period):
        raise exceptions.PermissionDenied('You do not have access to manage this slating period.', code='forbidden')
    offered = set(SLATING_BUILDER_TYPES)
    return _Form(
        kind='slating',
        builder=SLATING_FIELDS,
        rows=period.form_fields.all(),
        listing=lambda: slating_builder_fields(period),
        types=[t for t in period.form_fields.model.FIELD_TYPES if t[0] in offered],
        option_types=SLATING_OPTION_TYPES,
        name_pattern=r'^[a-z_]+$',
        title='Application form',
        web=reverse('slating_form_builder', args=[period.id]),
        scope={'period': period},
        unique_scope={'period': period},
        placeholder=False,
        defaults=True,
        warning=NOT_SETUP_WARNING if period.status != 'setup' else None,
        period=period,
        extra={'period': {'id': period.id, 'name': period.name, 'status': period.status,
                          'status_label': period.get_status_display()}},
    )


# ── JSON ────────────────────────────────────────────────────────────────────

def _field_json(form, f):
    builtin = bool(getattr(f, 'is_builtin', False))
    out = {
        'id': f.id,
        'field_name': f.field_name,
        'label': f.label,
        'field_type': f.field_type,
        'field_type_label': str(f.get_field_type_display()),
        'placeholder': f.placeholder,
        'help_text': f.help_text,
        'section': f.section,
        'is_required': f.is_required,
        'is_active': f.is_active,
        'is_builtin': builtin,
        'options': option_labels(f.options),
        'label_locked': builtin and form.builder.builtin_label_locked,
        'can_remove': f.is_active and not builtin,
        'can_restore': not f.is_active,
    }
    if form.builder.review_flags:
        out['show_in_review'] = f.show_in_review
    return out


def _form_json(form):
    active, inactive = form.listing()
    return {
        'kind': form.kind,
        'title': form.title,
        'fields': [_field_json(form, f) for f in active],
        'inactive': [_field_json(form, f) for f in inactive],
        'field_types': [{'value': v, 'label': str(lbl)} for v, lbl in form.types],
        'option_types': list(form.option_types),
        'name_pattern': form.name_pattern,
        'features': {
            'placeholder': form.placeholder,
            'show_in_review': form.builder.review_flags,
            'defaults': form.defaults,
        },
        'warning': form.warning,
        'web': form.web,
        **form.extra,
    }


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _text(body, key, label, max_length, required=False):
    value = body.get(key, '')
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise BadRequest(f'{label} must be text.', code='invalid_input')
    value = value.strip()
    if required and not value:
        raise BadRequest(f'{label} is required.', code='invalid_input')
    if len(value) > max_length:
        raise BadRequest(f'{label} is too long (at most {max_length} characters).', code='invalid_input')
    return value


def _flag(body, key, default=False):
    value = body.get(key, default)
    if not isinstance(value, bool):
        raise BadRequest(f'{key} must be true or false.', code='invalid_input')
    return value


def _options(body):
    raw = body.get('options', [])
    if isinstance(raw, str):
        raw = raw.split('\n')
    if not isinstance(raw, list) or not all(isinstance(o, str) for o in raw):
        raise BadRequest('Options must be a list of text.', code='invalid_input')
    options = [o.strip() for o in raw if o.strip()]
    if len(options) > OPTIONS_MAX:
        raise BadRequest(f'At most {OPTIONS_MAX} options.', code='invalid_input')
    if any(len(o) > OPTION_MAX for o in options):
        raise BadRequest(f'An option is too long (at most {OPTION_MAX} characters).', code='invalid_input')
    return options


def _input(form, body, field=None):
    """
    The page's form as the ``QueryDict`` its POST would carry, after the
    checks the browser makes there. ``field`` is the field being edited.
    """
    data = QueryDict(mutable=True)
    field_type = field.field_type if field else body.get('field_type')
    if field is None:
        if field_type not in dict(form.types):
            raise BadRequest('Choose a field type.', code='invalid_input')
        name = normalize_field_name(_text(body, 'field_name', 'Field name', NAME_MAX, required=True))
        if not re.match(form.name_pattern, name):
            allowed = 'Lowercase letters and underscores' if form.name_pattern == r'^[a-z_]+$' \
                else 'Lowercase letters, numbers and underscores'
            raise BadRequest(f'Field name: {allowed} only.', code='invalid_input')
        data['field_name'] = name
        data['field_type'] = field_type
    label_locked = field is not None and form.builder.builtin_label_locked and getattr(field, 'is_builtin', False)
    if not label_locked:
        data['label'] = _text(body, 'label', 'Label', LABEL_MAX, required=True)
    data['section'] = _text(body, 'section', 'Section', SECTION_MAX)
    data['help_text'] = _text(body, 'help_text', 'Help text', HELP_MAX)
    data['placeholder'] = _text(body, 'placeholder', 'Placeholder', PLACEHOLDER_MAX) if form.placeholder else ''
    if _flag(body, 'is_required'):
        data['is_required'] = 'on'
    if form.builder.review_flags:
        data['show_in_review'] = 'on' if _flag(body, 'show_in_review', True) else ''
        # The page has no box for it; keep what the field has rather than clear it.
        if field is not None and field.is_confidential:
            data['is_confidential'] = 'on'
    if field_type in form.option_types:
        options = _options(body)
        if not options:
            raise BadRequest('Add at least one option.', code='invalid_input')
        data['options'] = '\n'.join(options)
    if field is not None and form.builder.file_settings and field_type in ('file', 'image'):
        # The page has no boxes for these either: save them as they are.
        data.setlist('allowed_file_types', [str(t) for t in field.allowed_file_types or []])
        data['max_file_size_mb'] = str(field.max_file_size_mb)
    return data


def _refused(e):
    raise BadRequest(str(e), code=e.code)


# ── Views ───────────────────────────────────────────────────────────────────

class _BuilderView(MobileAPIView):
    web_page_toggle = False   # none of the builder pages has a page toggle
    parser_classes = [JSONParser]

    def form(self, request, **kwargs):
        raise NotImplementedError

    def field(self, form, field_id):
        return get_object_or_404(form.rows, id=field_id)

    @staticmethod
    def answer(form, message, level='success', status=200, field=None):
        out = {**_form_json(form), 'message': message, 'level': level}
        if field is not None:
            out['field'] = _field_json(form, field)
        return Response(out, status=status)


class _FormView(_BuilderView):

    def get(self, request, **kwargs):
        return Response(_form_json(self.form(request, **kwargs)))

    def post(self, request, **kwargs):
        form = self.form(request, **kwargs)
        data = _input(form, _body(request))
        try:
            f = add_field(form.builder, data, user=request.user, scope=form.scope, unique_scope=form.unique_scope)
        except FieldBuilderError as e:
            _refused(e)
        return self.answer(form, f'Field "{f.label}" added successfully.', status=201, field=f)


class _ReorderView(_BuilderView):

    def post(self, request, **kwargs):
        form = self.form(request, **kwargs)
        order = _body(request).get('order')
        if (not isinstance(order, list) or len(order) > ORDER_MAX
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in order)):
            raise BadRequest('order must be a list of field ids.', code='invalid_input')
        if len(set(order)) != len(order) or form.rows.filter(id__in=order).count() != len(order):
            raise BadRequest('That order names a field that isn’t on this form.', code='invalid_input')
        apply_field_order(form.rows, order)
        return self.answer(form, 'Field order saved.')


class _FieldView(_BuilderView):

    def get(self, request, field_id, **kwargs):
        form = self.form(request, **kwargs)
        return Response({'field': _field_json(form, self.field(form, field_id))})

    def post(self, request, field_id, **kwargs):
        form = self.form(request, **kwargs)
        f = self.field(form, field_id)
        update_field(form.builder, f, _input(form, _body(request), field=f), user=request.user)
        return self.answer(form, f'Field "{f.label}" updated.', field=f)


class _RemoveView(_BuilderView):

    def post(self, request, field_id, **kwargs):
        form = self.form(request, **kwargs)
        f = self.field(form, field_id)
        if not f.is_active:
            raise BadRequest('That field is already removed.', code='invalid_input')
        try:
            remove_field(form.builder, f, user=request.user)
        except FieldBuilderError as e:
            _refused(e)
        return self.answer(form, f'Field "{f.label}" removed.')


class _ToggleView(_BuilderView):

    def post(self, request, field_id, **kwargs):
        form = self.form(request, **kwargs)
        f = self.field(form, field_id)
        level, message = toggle_field(form.builder, f)
        if level != 'success':
            raise BadRequest(message, code='builtin')
        return self.answer(form, message, field=f)


class _DefaultsView(_BuilderView):

    def post(self, request, **kwargs):
        form = self.form(request, **kwargs)
        level, message = add_default_fields(form.period, request.user)
        return self.answer(form, message, level=level)


class _Kai:
    web_page = 'kai_form_builder'

    def form(self, request, form_type):
        return _kai_form(request, form_type)


class _Service:
    web_page = 'service_form_builder'

    def form(self, request):
        return _service_form(request)


class _Slating:
    web_page = 'slating_form_builder'

    def form(self, request, period_id):
        return _slating_form(request, period_id)


def _routes(prefix, mixin, name, defaults=False):
    def view(base):
        return type(f'{mixin.__name__.strip("_")}{base.__name__.strip("_")}', (mixin, base), {}).as_view()

    routes = [
        path(prefix, view(_FormView), name=f'mobile_{name}'),
        path(f'{prefix}reorder/', view(_ReorderView), name=f'mobile_{name}_reorder'),
        path(f'{prefix}fields/<int:field_id>/', view(_FieldView), name=f'mobile_{name}_field'),
        path(f'{prefix}fields/<int:field_id>/remove/', view(_RemoveView), name=f'mobile_{name}_remove'),
        path(f'{prefix}fields/<int:field_id>/toggle/', view(_ToggleView), name=f'mobile_{name}_toggle'),
    ]
    if defaults:
        routes.append(path(f'{prefix}defaults/', view(_DefaultsView), name=f'mobile_{name}_defaults'))
    return routes


#: Included by src/mobile/urls.py.
urlpatterns = [
    *_routes('kai/form-builder/<str:form_type>/', _Kai, 'kai_form_builder'),
    *_routes('officer/service-hours/form-builder/', _Service, 'service_form_builder'),
    *_routes('slating/periods/<int:period_id>/form-builder/', _Slating, 'slating_form_builder', defaults=True),
]
