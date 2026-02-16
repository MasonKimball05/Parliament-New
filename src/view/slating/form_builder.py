"""
Slating Form Builder View

Dynamic form builder for creating application forms.
Chair/admin can add, edit, reorder, and remove form fields.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
from src.models import SlatingPeriod, SlatingFormField, SlatingActivity
from .permissions import slating_chair_required


@login_required
@slating_chair_required
def form_builder(request, period_id):
    """
    Dynamic form builder for slating applications.
    Chair/admin can add, edit, reorder, and remove form fields.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Warn if not in setup phase
    if period.status != 'setup':
        messages.warning(request, 'Form can only be fully edited during setup phase. Some changes may affect existing applications.')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_field':
            return _handle_add_field(request, period)
        elif action == 'update_field':
            return _handle_update_field(request, period)
        elif action == 'delete_field':
            return _handle_delete_field(request, period)
        elif action == 'toggle_field':
            return _handle_toggle_field(request, period)
        elif action == 'add_default_fields':
            return _handle_add_default_fields(request, period)

        return redirect('slating_form_builder', period_id=period_id)

    # GET request - show form builder
    fields = period.form_fields.filter(is_active=True).order_by('display_order')

    # Group by section
    sections = {}
    for field in fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    # Get inactive fields
    inactive_fields = period.form_fields.filter(is_active=False)

    context = {
        'period': period,
        'fields': fields,
        'sections': sections,
        'inactive_fields': inactive_fields,
        'field_types': SlatingFormField.FIELD_TYPES,
    }

    return render(request, 'slating/form_builder.html', context)


def _handle_add_field(request, period):
    """Add a new form field."""
    field_name = request.POST.get('field_name', '').strip().lower().replace(' ', '_')
    label = request.POST.get('label', '').strip()
    field_type = request.POST.get('field_type', 'text')

    if not field_name or not label:
        messages.error(request, 'Field name and label are required.')
        return redirect('slating_form_builder', period_id=period.id)

    # Check for duplicate field name
    if period.form_fields.filter(field_name=field_name).exists():
        messages.error(request, f'A field with name "{field_name}" already exists.')
        return redirect('slating_form_builder', period_id=period.id)

    # Build field data
    field_data = {
        'field_name': field_name,
        'label': label,
        'field_type': field_type,
        'is_required': request.POST.get('is_required') == 'on',
        'placeholder': request.POST.get('placeholder', ''),
        'help_text': request.POST.get('help_text', ''),
        'section': request.POST.get('section', ''),
        'show_in_review': request.POST.get('show_in_review', 'on') == 'on',
        'is_confidential': request.POST.get('is_confidential') == 'on',
        'display_order': period.form_fields.count(),
    }

    # Handle options for select/radio/checkbox
    if field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        field_data['options'] = [o.strip() for o in options_text.split('\n') if o.strip()]

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
    field = SlatingFormField.objects.create(period=period, **field_data)

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='form_field_added',
        details=f'Added form field: {label} ({field_type})',
        metadata={'field_id': field.id, 'field_name': field_name},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Field "{label}" added successfully.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_update_field(request, period):
    """Update an existing form field."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(SlatingFormField, id=field_id, period=period)

    field.label = request.POST.get('label', field.label).strip()
    field.is_required = request.POST.get('is_required') == 'on'
    field.placeholder = request.POST.get('placeholder', '')
    field.help_text = request.POST.get('help_text', '')
    field.section = request.POST.get('section', '')
    field.show_in_review = request.POST.get('show_in_review', 'on') == 'on'
    field.is_confidential = request.POST.get('is_confidential') == 'on'

    # Update options
    if field.field_type in ['select', 'multiselect', 'radio', 'checkbox']:
        options_text = request.POST.get('options', '')
        field.options = [o.strip() for o in options_text.split('\n') if o.strip()]

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

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='form_field_modified',
        details=f'Updated form field: {field.label}',
        metadata={'field_id': field.id},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Field "{field.label}" updated.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_delete_field(request, period):
    """Delete (deactivate) a form field."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(SlatingFormField, id=field_id, period=period)

    label = field.label

    # Soft delete - just deactivate
    field.is_active = False
    field.save()

    messages.success(request, f'Field "{label}" removed.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_toggle_field(request, period):
    """Toggle field active status."""
    field_id = request.POST.get('field_id')
    field = get_object_or_404(SlatingFormField, id=field_id, period=period)

    field.is_active = not field.is_active
    field.save()

    status = 'activated' if field.is_active else 'deactivated'
    messages.success(request, f'Field "{field.label}" {status}.')
    return redirect('slating_form_builder', period_id=period.id)


@login_required
@require_http_methods(["POST"])
def reorder_fields(request):
    """
    API endpoint for drag-and-drop field reordering.
    """
    try:
        period_id = request.POST.get('period_id')
        order_data = json.loads(request.POST.get('order', '[]'))

        period = get_object_or_404(SlatingPeriod, id=period_id)

        # Check permission
        if not request.user.is_admin:
            if not (period.slating_committee and period.slating_committee.is_chair(request.user)):
                return JsonResponse({'status': 'error', 'message': 'Permission denied'}, status=403)

        # Update order
        for idx, field_id in enumerate(order_data):
            SlatingFormField.objects.filter(id=field_id, period=period).update(display_order=idx)

        return JsonResponse({'status': 'success'})

    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


def _handle_add_default_fields(request, period):
    """Add default form fields for a standard officer application."""

    # Check if active fields already exist
    if period.form_fields.filter(is_active=True).exists():
        messages.warning(request, 'This form already has fields. Default fields were not added to avoid duplicates.')
        return redirect('slating_form_builder', period_id=period.id)

    # Default fields for officer applications
    default_fields = [
        {
            'field_name': 'position_preference',
            'label': 'Position Preference',
            'field_type': 'position_preference',
            'section': 'Position Selection',
            'help_text': 'Rank the positions you are interested in, with 1 being your top choice.',
            'is_required': True,
            'show_in_review': True,
            'display_order': 0,
        },
        {
            'field_name': 'gpa',
            'label': 'Current GPA',
            'field_type': 'gpa',
            'section': 'Eligibility',
            'help_text': 'Enter your current cumulative GPA and upload a screenshot from your student portal.',
            'is_required': True,
            'show_in_review': True,
            'display_order': 1,
        },
        {
            'field_name': 'why_position',
            'label': 'Why are you interested in this position?',
            'field_type': 'textarea',
            'section': 'Application Questions',
            'help_text': 'Explain your motivation and what you hope to accomplish.',
            'is_required': True,
            'show_in_review': True,
            'display_order': 2,
        },
        {
            'field_name': 'qualifications',
            'label': 'What qualifications and experience do you have for this role?',
            'field_type': 'textarea',
            'section': 'Application Questions',
            'help_text': 'Describe relevant experience, skills, and accomplishments.',
            'is_required': True,
            'show_in_review': True,
            'display_order': 3,
        },
        {
            'field_name': 'goals',
            'label': 'What goals would you set for yourself in this position?',
            'field_type': 'textarea',
            'section': 'Application Questions',
            'help_text': 'Be specific about what you want to achieve.',
            'is_required': True,
            'show_in_review': True,
            'display_order': 4,
        },
        {
            'field_name': 'time_commitment',
            'label': 'Are you able to commit the time required for this position?',
            'field_type': 'radio',
            'section': 'Commitment',
            'help_text': 'Officer positions require significant time investment.',
            'is_required': True,
            'show_in_review': True,
            'options': ['Yes, I understand and can commit the time', 'I have concerns about time commitment'],
            'display_order': 5,
        },
        {
            'field_name': 'other_commitments',
            'label': 'List any other significant commitments (jobs, other organizations, etc.)',
            'field_type': 'textarea',
            'section': 'Commitment',
            'help_text': 'Help us understand your availability.',
            'is_required': False,
            'show_in_review': True,
            'display_order': 6,
        },
        {
            'field_name': 'additional_info',
            'label': 'Is there anything else you would like the slating committee to know?',
            'field_type': 'textarea',
            'section': 'Additional Information',
            'help_text': 'Optional - share any other relevant information.',
            'is_required': False,
            'show_in_review': True,
            'display_order': 7,
        },
    ]

    # Create or reactivate default fields
    created_count = 0
    reactivated_count = 0
    for field_data in default_fields:
        field_name = field_data.pop('field_name')
        field, created = SlatingFormField.objects.update_or_create(
            period=period,
            field_name=field_name,
            defaults={**field_data, 'is_active': True}
        )
        if created:
            created_count += 1
        else:
            reactivated_count += 1

    # Log activity
    total_count = created_count + reactivated_count
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='form_field_added',
        details=f'Added {created_count} new, reactivated {reactivated_count} default form fields',
        ip_address=request.META.get('REMOTE_ADDR')
    )

    if reactivated_count > 0 and created_count > 0:
        messages.success(request, f'Added {created_count} new fields and restored {reactivated_count} previously deleted fields.')
    elif reactivated_count > 0:
        messages.success(request, f'Restored {reactivated_count} previously deleted fields.')
    else:
        messages.success(request, f'Added {created_count} default fields to the form.')
    return redirect('slating_form_builder', period_id=period.id)


@login_required
@slating_chair_required
def get_field_details(request, period_id, field_id):
    """
    API endpoint to get field details for editing modal.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    field = get_object_or_404(SlatingFormField, id=field_id, period=period)

    data = {
        'id': field.id,
        'field_name': field.field_name,
        'label': field.label,
        'field_type': field.field_type,
        'placeholder': field.placeholder,
        'help_text': field.help_text,
        'section': field.section,
        'is_required': field.is_required,
        'show_in_review': field.show_in_review,
        'is_confidential': field.is_confidential,
        'options': field.options,
        'allowed_file_types': field.allowed_file_types,
        'max_file_size_mb': field.max_file_size_mb,
        'validation_rules': field.validation_rules,
    }

    return JsonResponse(data)
