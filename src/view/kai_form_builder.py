"""
Kai Form Builder Views

Dynamic form builder for customizing Kai report forms.
Chair/admin can add, edit, reorder, and remove form fields.
Built-in fields (title, category, description, etc.) cannot be deleted.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
import logging

from src.models import Committee, KaiFormField, KaiReportActivity
from src.decorators import kai_chair_required
from src.view.form_field_builder import (
    FieldBuilder, FieldBuilderError, add_field, apply_field_order, remove_field, toggle_field, update_field,
)

logger = logging.getLogger('function_calls')


@login_required
@kai_chair_required
def kai_form_builder(request, form_type='discipline'):
    """
    Form builder for Kai committee chair to customize report/commendation
    form fields.

    v3.28.9: `form_type` (from the URL — see urls.py's
    `kai_commendation_form_builder` route) scopes EVERY query below to one
    form. Two url names route here (`kai_form_builder` defaults to
    'discipline', `kai_commendation_form_builder` passes 'commendation')
    rather than duplicating this whole view, because the two forms' field
    editor is identical in every way except which population of
    `KaiFormField` rows it's pointed at — see KaiFormField's own docstring
    for why the definitions are shared infrastructure.
    """
    if request.method == 'POST':
        # The template always resubmits form_type as a hidden field (rather
        # than trusting the URL alone) so a POST handler that only has
        # request.POST to work with — _handle_add_field, when it computes a
        # NEW field's display_order, for instance — knows which form it's
        # editing without re-deriving it from the Referer header.
        posted_form_type = request.POST.get('form_type', form_type)
        action = request.POST.get('action')

        if action == 'add_field':
            return _handle_add_field(request, posted_form_type)
        elif action == 'update_field':
            return _handle_update_field(request, posted_form_type)
        elif action == 'delete_field':
            return _handle_delete_field(request, posted_form_type)
        elif action == 'toggle_field':
            return _handle_toggle_field(request, posted_form_type)

        return redirect(_builder_url_name(posted_form_type))

    # GET request - show form builder
    fields, inactive_fields = kai_builder_fields(form_type)

    # Group by section
    sections = {}
    for field in fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    # Get Kai committee for context
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        kai_committee = None

    context = {
        'fields': fields,
        'sections': sections,
        'inactive_fields': inactive_fields,
        'field_types': KaiFormField.FIELD_TYPES,
        'kai_committee': kai_committee,
        'form_type': form_type,
        'other_form_type': 'commendation' if form_type == 'discipline' else 'discipline',
    }

    return render(request, 'kai/form_builder.html', context)


def _builder_url_name(form_type):
    return 'kai_commendation_form_builder' if form_type == 'commendation' else 'kai_form_builder'


def _log_change(user, action, field):
    verb = {'add': 'added', 'update': 'updated', 'remove': 'deleted'}[action]
    suffix = f' ({field.field_type})' if action == 'add' else ''
    logger.info(f"{user.username} {verb} Kai form field: {field.label}{suffix}")


#: The Kai forms' switches for the shared field editing (src/view/form_field_builder.py).
#: Field names are unique across both forms (see KaiFormField's docstring).
KAI_FIELDS = FieldBuilder(
    model=KaiFormField,
    options_as_dicts=True,
    file_settings=True,
    validation_rules=True,
    tracks_creator=True,
    builtin_label_locked=True,
    builtin_toggle_locked=True,
    log=_log_change,
)

#: The option boxes the page shows (it saves checkbox options too, but never shows the box for one).
KAI_OPTION_TYPES = ('select', 'multiselect', 'radio')


def kai_builder_allowed(user):
    """``kai_chair_required``'s rule: a Kai chair or a site admin; a site admin only when there's no committee."""
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        return bool(user.is_admin)
    return bool(kai_committee.is_chair(user) or user.is_admin)


def kai_builder_fields(form_type):
    """``(active, inactive)`` as the page lists them: active by section then order; inactive non-built-in."""
    active = KaiFormField.objects.filter(is_active=True, form_type=form_type).order_by('section', 'display_order')
    inactive = KaiFormField.objects.filter(is_active=False, is_builtin=False, form_type=form_type)
    return active, inactive


def _handle_add_field(request, form_type='discipline'):
    """Add a new form field.

    Display order is scoped to THIS form (v3.28.9): counting every field
    regardless of form_type would give a brand-new commendation field a
    display_order picking up wherever the (unrelated) discipline field count
    happened to leave off. The duplicate-name check is GLOBAL across both
    forms (field_name is unique on the model)."""
    try:
        field = add_field(KAI_FIELDS, request.POST, user=request.user, scope={'form_type': form_type})
    except FieldBuilderError as e:
        messages.error(request, str(e))
        return redirect(_builder_url_name(form_type))
    messages.success(request, f'Field "{field.label}" added successfully.')
    return redirect(_builder_url_name(form_type))


def _handle_update_field(request, form_type='discipline'):
    """Update an existing form field (built-in fields keep their label, name and type)."""
    field = get_object_or_404(KaiFormField, id=request.POST.get('field_id'))
    update_field(KAI_FIELDS, field, request.POST, user=request.user)
    messages.success(request, f'Field "{field.label}" updated.')
    return redirect(_builder_url_name(form_type))


def _handle_delete_field(request, form_type='discipline'):
    """Delete (deactivate) a form field. Built-in fields cannot be deleted."""
    field = get_object_or_404(KaiFormField, id=request.POST.get('field_id'))
    try:
        remove_field(KAI_FIELDS, field, user=request.user)
    except FieldBuilderError as e:
        messages.error(request, str(e))
        return redirect(_builder_url_name(form_type))
    messages.success(request, f'Field "{field.label}" removed.')
    return redirect(_builder_url_name(form_type))


def _handle_toggle_field(request, form_type='discipline'):
    """Toggle field active status (reactivate a deleted field)."""
    field = get_object_or_404(KaiFormField, id=request.POST.get('field_id'))
    level, message = toggle_field(KAI_FIELDS, field)
    getattr(messages, level)(request, message)
    return redirect(_builder_url_name(form_type))


@login_required
@require_http_methods(["POST"])
def reorder_kai_fields(request):
    """
    API endpoint for drag-and-drop field reordering.
    """
    try:
        # Check permission
        if not kai_builder_allowed(request.user):
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)

        order_data = json.loads(request.POST.get('order', '[]'))

        apply_field_order(KaiFormField.objects.all(), order_data)

        return JsonResponse({'status': 'success'})

    except Exception as e:
        logger.error(f"Error reordering Kai form fields: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
@kai_chair_required
def get_kai_field_details(request, field_id):
    """
    API endpoint to get field details for editing modal.
    """
    field = get_object_or_404(KaiFormField, id=field_id)

    # Format options for display
    options_display = ''
    if field.options:
        if isinstance(field.options, list):
            # Handle both old format (list of strings) and new format (list of dicts)
            for opt in field.options:
                if isinstance(opt, dict):
                    options_display += opt.get('label', opt.get('value', '')) + '\n'
                else:
                    options_display += str(opt) + '\n'

    data = {
        'id': field.id,
        'field_name': field.field_name,
        'label': field.label,
        'field_type': field.field_type,
        'placeholder': field.placeholder,
        'help_text': field.help_text,
        'section': field.section,
        'is_required': field.is_required,
        'is_builtin': field.is_builtin,
        'options': options_display.strip(),
        'allowed_file_types': field.allowed_file_types,
        'max_file_size_mb': field.max_file_size_mb,
        'validation_rules': field.validation_rules,
    }

    return JsonResponse(data)
