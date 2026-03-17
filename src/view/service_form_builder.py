"""
Service Hours Form Builder Views

Dynamic form builder for customizing service hours submission forms.
VPP/admin can add, edit, reorder, and remove form fields.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
import logging

from src.models import ServiceFormField
from src.decorators import vpp_required

logger = logging.getLogger('function_calls')


def _ensure_builtin_fields():
    """Create built-in fields if they don't exist."""
    builtin_fields = [
        {
            'field_name': 'period',
            'label': 'Service Period',
            'field_type': 'select',
            'is_required': True,
            'help_text': 'Select the period for this service',
            'display_order': 0,
            'section': '',
            'is_builtin': True,
        },
        {
            'field_name': 'hours',
            'label': 'Hours',
            'field_type': 'number',
            'is_required': True,
            'help_text': 'Enter hours in increments of 0.25',
            'placeholder': '0.00',
            'display_order': 1,
            'section': '',
            'is_builtin': True,
        },
        {
            'field_name': 'service_date',
            'label': 'Date of Service',
            'field_type': 'date',
            'is_required': True,
            'help_text': '',
            'display_order': 2,
            'section': '',
            'is_builtin': True,
        },
        {
            'field_name': 'organization',
            'label': 'Organization/Event',
            'field_type': 'text',
            'is_required': True,
            'help_text': 'Name of the organization or event where you volunteered',
            'placeholder': '',
            'display_order': 3,
            'section': '',
            'is_builtin': True,
        },
        {
            'field_name': 'description',
            'label': 'Description',
            'field_type': 'textarea',
            'is_required': True,
            'help_text': 'Describe the service you performed',
            'placeholder': '',
            'display_order': 4,
            'section': '',
            'is_builtin': True,
        },
        {
            'field_name': 'attachment',
            'label': 'Proof/Documentation',
            'field_type': 'file',
            'is_required': False,
            'help_text': 'Upload a photo, certificate, or other proof (PDF, JPG, PNG, DOCX - max 20MB)',
            'display_order': 5,
            'section': '',
            'is_builtin': True,
        },
    ]

    for field_data in builtin_fields:
        ServiceFormField.objects.get_or_create(
            field_name=field_data['field_name'],
            defaults=field_data
        )


@login_required
@vpp_required
def service_form_builder(request):
    """
    Form builder for VPP to customize service hours submission form fields.
    """
    # Ensure built-in fields exist
    _ensure_builtin_fields()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_field':
            return _handle_add_field(request)
        elif action == 'update_field':
            return _handle_update_field(request)
        elif action == 'delete_field':
            return _handle_delete_field(request)
        elif action == 'toggle_field':
            return _handle_toggle_field(request)

        return redirect('service_form_builder')

    # GET request - show form builder
    fields = ServiceFormField.objects.filter(is_active=True).order_by('display_order', 'section')

    # Group by section
    sections = {}
    for field in fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    # Get inactive fields (excluding built-in which should always be restorable)
    inactive_fields = ServiceFormField.objects.filter(is_active=False)

    context = {
        'fields': fields,
        'sections': sections,
        'inactive_fields': inactive_fields,
        'field_types': ServiceFormField.FIELD_TYPES,
    }

    return render(request, 'service_hours/form_builder.html', context)


def _handle_add_field(request):
    """Add a new form field."""
    field_name = request.POST.get('field_name', '').strip().lower().replace(' ', '_')
    label = request.POST.get('label', '').strip()
    field_type = request.POST.get('field_type', 'text')

    if not field_name or not label:
        messages.error(request, 'Field name and label are required.')
        return redirect('service_form_builder')

    # Check for duplicate field name
    if ServiceFormField.objects.filter(field_name=field_name).exists():
        messages.error(request, f'A field with name "{field_name}" already exists.')
        return redirect('service_form_builder')

    # Build field data
    field_data = {
        'field_name': field_name,
        'label': label,
        'field_type': field_type,
        'is_required': request.POST.get('is_required') == 'on',
        'placeholder': request.POST.get('placeholder', ''),
        'help_text': request.POST.get('help_text', ''),
        'section': request.POST.get('section', ''),
        'display_order': ServiceFormField.objects.count(),
        'created_by': request.user,
    }

    # Handle options for select/radio/checkbox
    if field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        options_list = [o.strip() for o in options_text.split('\n') if o.strip()]
        field_data['options'] = options_list

    # Create the field
    field = ServiceFormField.objects.create(**field_data)

    logger.info(f"{request.user.username} added service form field: {label} ({field_type})")
    messages.success(request, f'Field "{label}" added successfully.')
    return redirect('service_form_builder')


def _handle_update_field(request):
    """Update an existing form field."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(ServiceFormField, id=field_id)

    field.label = request.POST.get('label', field.label).strip()
    field.is_required = request.POST.get('is_required') == 'on'
    field.placeholder = request.POST.get('placeholder', '')
    field.help_text = request.POST.get('help_text', '')
    field.section = request.POST.get('section', '')

    # Update options (for select/radio/checkbox)
    if field.field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        options_list = [o.strip() for o in options_text.split('\n') if o.strip()]
        field.options = options_list

    field.save()

    logger.info(f"{request.user.username} updated service form field: {field.label}")
    messages.success(request, f'Field "{field.label}" updated.')
    return redirect('service_form_builder')


def _handle_delete_field(request):
    """Delete (deactivate) a form field."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(ServiceFormField, id=field_id)

    # Prevent deleting built-in fields
    if field.is_builtin:
        messages.error(request, 'Built-in fields cannot be deleted.')
        return redirect('service_form_builder')

    label = field.label

    # Soft delete - just deactivate
    field.is_active = False
    field.save()

    logger.info(f"{request.user.username} deleted service form field: {label}")
    messages.success(request, f'Field "{label}" removed.')
    return redirect('service_form_builder')


def _handle_toggle_field(request):
    """Toggle field active status (reactivate a deleted field)."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(ServiceFormField, id=field_id)

    field.is_active = not field.is_active
    field.save()
    status = 'activated' if field.is_active else 'deactivated'
    messages.success(request, f'Field "{field.label}" {status}.')

    return redirect('service_form_builder')


@login_required
@require_http_methods(["POST"])
def reorder_service_fields(request):
    """
    API endpoint for drag-and-drop field reordering.
    """
    try:
        # Check permission
        if not request.user.is_admin and not request.user.roles.filter(code='VPP').exists():
            return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)

        order_data = json.loads(request.POST.get('order', '[]'))

        # Update order
        for idx, field_id in enumerate(order_data):
            ServiceFormField.objects.filter(id=field_id).update(display_order=idx)

        return JsonResponse({'status': 'success'})

    except Exception as e:
        logger.error(f"Error reordering service form fields: {str(e)}")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
@vpp_required
def get_service_field_details(request, field_id):
    """
    API endpoint to get field details for editing modal.
    """
    field = get_object_or_404(ServiceFormField, id=field_id)

    # Format options for display
    options_display = ''
    if field.options:
        if isinstance(field.options, list):
            options_display = '\n'.join(field.options)

    data = {
        'id': field.id,
        'field_name': field.field_name,
        'label': field.label,
        'field_type': field.field_type,
        'placeholder': field.placeholder,
        'help_text': field.help_text,
        'section': field.section,
        'is_required': field.is_required,
        'options': options_display.strip(),
    }

    return JsonResponse(data)
