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
    fields = KaiFormField.objects.filter(is_active=True, form_type=form_type).order_by('section', 'display_order')

    # Group by section
    sections = {}
    for field in fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    # Get inactive fields (excluding built-in, which should never be inactive)
    inactive_fields = KaiFormField.objects.filter(is_active=False, is_builtin=False, form_type=form_type)

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


def _handle_add_field(request, form_type='discipline'):
    """Add a new form field."""
    field_name = request.POST.get('field_name', '').strip().lower().replace(' ', '_')
    label = request.POST.get('label', '').strip()
    field_type = request.POST.get('field_type', 'text')

    if not field_name or not label:
        messages.error(request, 'Field name and label are required.')
        return redirect(_builder_url_name(form_type))

    # Check for duplicate field name — GLOBAL across both forms (field_name
    # is unique on the model regardless of form_type; see KaiFormField's
    # docstring on why that's the right scope).
    if KaiFormField.objects.filter(field_name=field_name).exists():
        messages.error(request, f'A field with name "{field_name}" already exists.')
        return redirect(_builder_url_name(form_type))

    # Build field data
    field_data = {
        'form_type': form_type,
        'field_name': field_name,
        'label': label,
        'field_type': field_type,
        'is_required': request.POST.get('is_required') == 'on',
        'placeholder': request.POST.get('placeholder', ''),
        'help_text': request.POST.get('help_text', ''),
        'section': request.POST.get('section', ''),
        # Scoped to THIS form — v3.28.9. Counting every field regardless of
        # form_type would give a brand-new commendation field a
        # display_order picking up wherever the (unrelated) discipline
        # field count happened to leave off.
        'display_order': KaiFormField.objects.filter(form_type=form_type).count(),
        'created_by': request.user,
        'is_builtin': False,  # User-created fields are never built-in
    }

    # Handle options for select/radio/checkbox
    if field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        options_list = [o.strip() for o in options_text.split('\n') if o.strip()]
        # Store as list of dicts with value and label
        field_data['options'] = [{'value': o.lower().replace(' ', '_'), 'label': o} for o in options_list]

    # Handle file type restrictions
    if field_type in ['file', 'image']:
        allowed_types = request.POST.getlist('allowed_file_types')
        if allowed_types:
            field_data['allowed_file_types'] = allowed_types
        try:
            field_data['max_file_size_mb'] = int(request.POST.get('max_file_size_mb', 10))
        except (ValueError, TypeError):
            field_data['max_file_size_mb'] = 10

    # Handle validation rules
    validation_rules = []
    min_length = request.POST.get('min_length')
    max_length = request.POST.get('max_length')
    min_value = request.POST.get('min_value')
    max_value = request.POST.get('max_value')

    if min_length:
        try:
            validation_rules.append({'type': 'min_length', 'value': int(min_length)})
        except ValueError:
            pass
    if max_length:
        try:
            validation_rules.append({'type': 'max_length', 'value': int(max_length)})
        except ValueError:
            pass
    if min_value:
        try:
            validation_rules.append({'type': 'min_value', 'value': float(min_value)})
        except ValueError:
            pass
    if max_value:
        try:
            validation_rules.append({'type': 'max_value', 'value': float(max_value)})
        except ValueError:
            pass

    if validation_rules:
        field_data['validation_rules'] = validation_rules

    # Create the field
    field = KaiFormField.objects.create(**field_data)

    logger.info(f"{request.user.username} added Kai form field: {label} ({field_type})")
    messages.success(request, f'Field "{label}" added successfully.')
    return redirect(_builder_url_name(form_type))


def _handle_update_field(request, form_type='discipline'):
    """Update an existing form field."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(KaiFormField, id=field_id)

    # Built-in fields have restricted editing (can't change field_name or field_type)
    if not field.is_builtin:
        field.label = request.POST.get('label', field.label).strip()

    field.is_required = request.POST.get('is_required') == 'on'
    field.placeholder = request.POST.get('placeholder', '')
    field.help_text = request.POST.get('help_text', '')
    field.section = request.POST.get('section', '')

    # Update options (for select/radio/checkbox)
    if field.field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        options_list = [o.strip() for o in options_text.split('\n') if o.strip()]
        field.options = [{'value': o.lower().replace(' ', '_'), 'label': o} for o in options_list]

    # Update file settings
    if field.field_type in ['file', 'image']:
        allowed_types = request.POST.getlist('allowed_file_types')
        if allowed_types:
            field.allowed_file_types = allowed_types
        try:
            field.max_file_size_mb = int(request.POST.get('max_file_size_mb', 10))
        except (ValueError, TypeError):
            pass

    field.save()

    logger.info(f"{request.user.username} updated Kai form field: {field.label}")
    messages.success(request, f'Field "{field.label}" updated.')
    return redirect(_builder_url_name(form_type))


def _handle_delete_field(request, form_type='discipline'):
    """Delete (deactivate) a form field. Built-in fields cannot be deleted."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(KaiFormField, id=field_id)

    if field.is_builtin:
        messages.error(request, f'"{field.label}" is a built-in field and cannot be removed.')
        return redirect(_builder_url_name(form_type))

    label = field.label

    # Soft delete - just deactivate
    field.is_active = False
    field.save(update_fields=['is_active'])

    logger.info(f"{request.user.username} deleted Kai form field: {label}")
    messages.success(request, f'Field "{label}" removed.')
    return redirect(_builder_url_name(form_type))


def _handle_toggle_field(request, form_type='discipline'):
    """Toggle field active status (reactivate a deleted field)."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(KaiFormField, id=field_id)

    if field.is_builtin and not field.is_active:
        # Built-in fields should always be active, reactivate them
        field.is_active = True
        field.save(update_fields=['is_active'])
        messages.success(request, f'Field "{field.label}" restored.')
    elif not field.is_builtin:
        field.is_active = not field.is_active
        field.save(update_fields=['is_active'])
        status = 'activated' if field.is_active else 'deactivated'
        messages.success(request, f'Field "{field.label}" {status}.')
    else:
        messages.warning(request, f'"{field.label}" is a built-in field and cannot be deactivated.')

    return redirect(_builder_url_name(form_type))


@login_required
@require_http_methods(["POST"])
def reorder_kai_fields(request):
    """
    API endpoint for drag-and-drop field reordering.
    """
    try:
        # Check permission
        try:
            kai_committee = Committee.objects.get(is_kai_committee=True)
            if not kai_committee.is_chair(request.user) and not request.user.is_admin:
                return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)
        except Committee.DoesNotExist:
            if not request.user.is_admin:
                return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)

        order_data = json.loads(request.POST.get('order', '[]'))

        # Update order
        for idx, field_id in enumerate(order_data):
            KaiFormField.objects.filter(id=field_id).update(display_order=idx)

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
