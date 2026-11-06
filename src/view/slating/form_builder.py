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
from src.view.form_field_builder import (
    FieldBuilder, FieldBuilderError, add_field, remove_field, toggle_field, update_field,
)
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
        messages.warning(request, NOT_SETUP_WARNING)

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
    fields, inactive_fields = slating_builder_fields(period)

    # Group by section
    sections = {}
    for field in fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    context = {
        'period': period,
        'fields': fields,
        'sections': sections,
        'inactive_fields': inactive_fields,
        'field_types': SlatingFormField.FIELD_TYPES,
    }

    return render(request, 'slating/form_builder.html', context)


def _log_change(user, action, field):
    """The period's activity log, as the page has always written it (adds and edits only)."""
    if action == 'add':
        SlatingActivity.objects.create(
            period=field.period,
            user=user,
            action='form_field_added',
            details=f'Added form field: {field.label} ({field.field_type})',
            metadata={'field_id': field.id, 'field_name': field.field_name}
        )
    elif action == 'update':
        SlatingActivity.objects.create(
            period=field.period,
            user=user,
            action='form_field_modified',
            details=f'Updated form field: {field.label}',
            metadata={'field_id': field.id}
        )


#: The application form's switches for the shared field editing (src/view/form_field_builder.py).
SLATING_FIELDS = FieldBuilder(
    model=SlatingFormField,
    file_settings=True,
    validation_rules=True,
    review_flags=True,
    log=_log_change,
)

#: The field types the page's Add Field menu offers (the model knows a few more it never shows).
SLATING_BUILDER_TYPES = (
    'text', 'textarea', 'number', 'decimal', 'email', 'date', 'select', 'multiselect', 'checkbox', 'radio',
    'file', 'image', 'gpa', 'position_preference',
)
#: The option boxes the page shows.
SLATING_OPTION_TYPES = ('select', 'multiselect', 'radio')

#: The page's warning outside the setup stage (there is no lock: the form stays editable).
NOT_SETUP_WARNING = 'Form can only be fully edited during setup phase. Some changes may affect existing applications.'


def slating_builder_fields(period):
    """``(active, inactive)``: the page lists the active ones in display order."""
    return (period.form_fields.filter(is_active=True).order_by('display_order'),
            period.form_fields.filter(is_active=False))


def _handle_add_field(request, period):
    """Add a new form field."""
    try:
        field = add_field(SLATING_FIELDS, request.POST, user=request.user,
                          scope={'period': period}, unique_scope={'period': period})
    except FieldBuilderError as e:
        messages.error(request, str(e))
        return redirect('slating_form_builder', period_id=period.id)
    messages.success(request, f'Field "{field.label}" added successfully.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_update_field(request, period):
    """Update an existing form field."""
    field = get_object_or_404(SlatingFormField, id=request.POST.get('field_id'), period=period)
    update_field(SLATING_FIELDS, field, request.POST, user=request.user)
    messages.success(request, f'Field "{field.label}" updated.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_delete_field(request, period):
    """Delete (deactivate) a form field."""
    field = get_object_or_404(SlatingFormField, id=request.POST.get('field_id'), period=period)
    remove_field(SLATING_FIELDS, field, user=request.user)
    messages.success(request, f'Field "{field.label}" removed.')
    return redirect('slating_form_builder', period_id=period.id)


def _handle_toggle_field(request, period):
    """Toggle field active status."""
    field = get_object_or_404(SlatingFormField, id=request.POST.get('field_id'), period=period)
    level, message = toggle_field(SLATING_FIELDS, field)
    getattr(messages, level)(request, message)
    return redirect('slating_form_builder', period_id=period.id)


def _handle_add_default_fields(request, period):
    """Add default form fields for a standard officer application."""
    level, message = add_default_fields(period, request.user)
    getattr(messages, level)(request, message)
    return redirect('slating_form_builder', period_id=period.id)


def add_default_fields(period, user):
    """
    The page's "Add default fields": the standard officer application, only
    onto a form with no active fields (a previously deleted default comes
    back). ``(level, message)`` as the page flashes it.
    """
    # Check if active fields already exist
    if period.form_fields.filter(is_active=True).exists():
        return 'warning', 'This form already has fields. Default fields were not added to avoid duplicates.'

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
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='form_field_added',
        details=f'Added {created_count} new, reactivated {reactivated_count} default form fields'
    )

    if reactivated_count > 0 and created_count > 0:
        return 'success', (f'Added {created_count} new fields and restored {reactivated_count} '
                           'previously deleted fields.')
    elif reactivated_count > 0:
        return 'success', f'Restored {reactivated_count} previously deleted fields.'
    return 'success', f'Added {created_count} default fields to the form.'


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
