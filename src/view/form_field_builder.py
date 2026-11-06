"""
The form builders' shared field editing (11-06-26).

Three website pages let an officer shape a form members fill in, each over
its own field model:

* the Kai chair's report / commendation forms (``kai_form_builder``,
  ``KaiFormField``, src/view/kai_form_builder.py),
* the VPP's service hours form (``service_form_builder``,
  ``ServiceFormField``, src/view/service_form_builder.py),
* a slating period's application form (``slating_form_builder``,
  ``SlatingFormField``, src/view/slating/form_builder.py).

The three pages had grown three copies of the same add / edit / remove /
restore / reorder code, differing in a handful of switches (choices stored as
``{value, label}`` dicts or plain strings, file and validation settings, the
slating review flags, built-in fields). Those switches are a ``FieldBuilder``;
the functions here are the one copy, and the web pages and the app's
endpoints (src/mobile/views_form_builder.py) both call them, so a field saved
from the phone is exactly the field the website would have saved.

Each function reads the website form's own field names from ``data`` (a
``QueryDict``, or anything with ``get`` / ``getlist``) and raises
``FieldBuilderError`` with the page's own words when it refuses.
"""
from dataclasses import dataclass
from typing import Callable, Optional

#: The field types the builders show an options box for (they save options for checkbox too).
CHOICE_TYPES = ('select', 'multiselect', 'radio', 'checkbox')
FILE_TYPES = ('file', 'image')


class FieldBuilderError(Exception):
    """A refusal, with the message the website flashes."""

    def __init__(self, message, code='invalid_input'):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FieldBuilder:
    model: type
    #: Choices saved as ``[{value, label}]`` (Kai) rather than ``[label]``.
    options_as_dicts: bool = False
    #: Saves ``allowed_file_types`` / ``max_file_size_mb`` for file and image fields.
    file_settings: bool = False
    #: Saves ``min_length`` / ``max_length`` / ``min_value`` / ``max_value`` rules on add.
    validation_rules: bool = False
    #: Slating's ``show_in_review`` / ``is_confidential``.
    review_flags: bool = False
    #: Sets ``created_by`` on add.
    tracks_creator: bool = False
    #: A built-in field's label is fixed (Kai).
    builtin_label_locked: bool = False
    #: A built-in field can't be switched off, only restored (Kai). Service's toggle flips anything.
    builtin_toggle_locked: bool = False
    #: The refusal for removing a built-in field (``{label}`` filled in).
    builtin_remove_message: str = '"{label}" is a built-in field and cannot be removed.'
    #: ``log(user, action, field)`` after an add / update / remove (``'add'``, ``'update'``, ``'remove'``).
    log: Optional[Callable] = None

    def has_builtins(self):
        return any(f.name == 'is_builtin' for f in self.model._meta.get_fields())


def normalize_field_name(raw):
    """The internal name as every builder stores it: lowercased, spaces to underscores."""
    return (raw or '').strip().lower().replace(' ', '_')


def options_from_text(builder, text):
    """The options box (one per line) as the model stores them."""
    options = [o.strip() for o in (text or '').split('\n') if o.strip()]
    if builder.options_as_dicts:
        return [{'value': o.lower().replace(' ', '_'), 'label': o} for o in options]
    return options


def option_labels(options):
    """A stored options list as the labels the options box shows (old Kai rows are plain strings)."""
    out = []
    for opt in options or []:
        if isinstance(opt, dict):
            out.append(str(opt.get('label', opt.get('value', ''))))
        else:
            out.append(str(opt))
    return out


def _validation_rules_from(data):
    rules = []
    for key, kind in (('min_length', int), ('max_length', int), ('min_value', float), ('max_value', float)):
        raw = data.get(key)
        if raw:
            try:
                rules.append({'type': key, 'value': kind(raw)})
            except ValueError:
                pass
    return rules


def _log(builder, user, action, field):
    if builder.log is not None:
        builder.log(user, action, field)


def add_field(builder, data, *, user, scope=None, unique_scope=None):
    """
    The page's ``add_field``: a new field at the end of ``scope`` (the rows it
    counts for its display order: one Kai form, one slating period, or
    everything). ``unique_scope`` is where the internal name must be unused
    (Kai and service names are unique across the table; slating's per period).
    """
    scope = scope or {}
    unique_scope = unique_scope or {}
    model = builder.model
    field_name = normalize_field_name(data.get('field_name', ''))
    label = data.get('label', '').strip()
    field_type = data.get('field_type', 'text')

    if not field_name or not label:
        raise FieldBuilderError('Field name and label are required.')
    if model.objects.filter(field_name=field_name, **unique_scope).exists():
        raise FieldBuilderError(f'A field with name "{field_name}" already exists.', code='duplicate')

    values = {
        **scope,
        'field_name': field_name,
        'label': label,
        'field_type': field_type,
        'is_required': data.get('is_required') == 'on',
        'placeholder': data.get('placeholder', ''),
        'help_text': data.get('help_text', ''),
        'section': data.get('section', ''),
        'display_order': model.objects.filter(**scope).count(),
    }
    if builder.review_flags:
        values['show_in_review'] = data.get('show_in_review', 'on') == 'on'
        values['is_confidential'] = data.get('is_confidential') == 'on'
    if builder.tracks_creator:
        values['created_by'] = user
    if builder.has_builtins():
        values['is_builtin'] = False   # what the officer adds is never built in

    if field_type in CHOICE_TYPES:
        values['options'] = options_from_text(builder, data.get('options', ''))

    if builder.file_settings and field_type in FILE_TYPES:
        allowed_types = data.getlist('allowed_file_types')
        if allowed_types:
            values['allowed_file_types'] = allowed_types
        try:
            values['max_file_size_mb'] = int(data.get('max_file_size_mb', 10))
        except (ValueError, TypeError):
            values['max_file_size_mb'] = 10

    if builder.validation_rules:
        rules = _validation_rules_from(data)
        if rules:
            values['validation_rules'] = rules

    field = model.objects.create(**values)
    _log(builder, user, 'add', field)
    return field


def update_field(builder, field, data, *, user):
    """The page's ``update_field``. The internal name and type never change."""
    if not (builder.builtin_label_locked and getattr(field, 'is_builtin', False)):
        field.label = data.get('label', field.label).strip()
    field.is_required = data.get('is_required') == 'on'
    field.placeholder = data.get('placeholder', '')
    field.help_text = data.get('help_text', '')
    field.section = data.get('section', '')
    if builder.review_flags:
        field.show_in_review = data.get('show_in_review', 'on') == 'on'
        # 11-06-26: the website's edit form has no "confidential" input, so a
        # missing key keeps the setting (it used to turn it off on every edit).
        if 'is_confidential' in data:
            field.is_confidential = data.get('is_confidential') == 'on'

    if field.field_type in CHOICE_TYPES:
        field.options = options_from_text(builder, data.get('options', ''))

    if builder.file_settings and field.field_type in FILE_TYPES:
        allowed_types = data.getlist('allowed_file_types')
        if allowed_types:
            field.allowed_file_types = allowed_types
        # 11-06-26: likewise the size limit (an edit reset it to 10 MB).
        if 'max_file_size_mb' in data:
            try:
                field.max_file_size_mb = int(data.get('max_file_size_mb'))
            except (ValueError, TypeError):
                pass

    field.save()
    _log(builder, user, 'update', field)
    return field


def _save_active(field):
    update = ['is_active']
    if any(f.name == 'updated_at' for f in field._meta.get_fields()):
        update.append('updated_at')
    field.save(update_fields=update)


def remove_field(builder, field, *, user):
    """The page's ``delete_field``: a soft delete (the field goes inactive). Built-in fields stay."""
    if getattr(field, 'is_builtin', False):
        raise FieldBuilderError(builder.builtin_remove_message.format(label=field.label), code='builtin')
    field.is_active = False
    _save_active(field)
    _log(builder, user, 'remove', field)
    return field


def toggle_field(builder, field):
    """
    The page's ``toggle_field`` (its Restore button): ``(level, message)``,
    level being the ``django.contrib.messages`` function the page flashes it
    with. Kai's built-in fields only ever come back on.
    """
    if builder.builtin_toggle_locked and getattr(field, 'is_builtin', False):
        if field.is_active:
            return 'warning', f'"{field.label}" is a built-in field and cannot be deactivated.'
        field.is_active = True
        _save_active(field)
        return 'success', f'Field "{field.label}" restored.'
    field.is_active = not field.is_active
    _save_active(field)
    status = 'activated' if field.is_active else 'deactivated'
    return 'success', f'Field "{field.label}" {status}.'


def apply_field_order(queryset, field_ids):
    """The drag-and-drop reorder: each id's display order is its place in ``field_ids``."""
    for index, field_id in enumerate(field_ids):
        queryset.filter(id=field_id).update(display_order=index)
