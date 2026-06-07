"""
Slating Application Views

Application submission and management for candidates.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.exceptions import ValidationError
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingApplicationResponse,
    SlatingFormField, SlatingPosition, SlatingActivity
)
from src.decorators import exclude_pledges


@login_required
@exclude_pledges
def apply_view(request, period_id):
    """
    Dynamic application form submission.
    Form fields are defined by SlatingFormField model.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    # Check if applications are open
    if not period.can_apply():
        messages.error(request, 'Applications are not currently open for this period.')
        return redirect('slating_dashboard')

    # Check for existing application
    existing_app = SlatingApplication.objects.filter(
        period=period, applicant=user
    ).first()

    if existing_app and existing_app.status not in ['draft', 'withdrawn']:
        messages.info(request, 'You have already submitted an application.')
        return redirect('slating_my_applications')

    # Get form fields
    form_fields = period.form_fields.filter(is_active=True).order_by('display_order')
    positions = period.positions.filter(is_active=True).order_by('display_order')

    if request.method == 'POST':
        return _handle_application_submit(request, period, user, existing_app, form_fields, positions)

    # GET - Load existing responses if draft exists
    existing_responses = {}
    if existing_app:
        for response in existing_app.responses.select_related('field').all():
            existing_responses[response.field_id] = response

    # Group fields by section
    sections = {}
    for field in form_fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)

    context = {
        'period': period,
        'fields': form_fields,  # Template uses 'fields' for regroup
        'form_fields': form_fields,  # Keep for compatibility
        'sections': sections,
        'positions': positions,
        'application': existing_app,
        'responses': existing_responses,  # Template uses 'responses'
    }

    return render(request, 'slating/apply.html', context)


def _handle_application_submit(request, period, user, existing_app, form_fields, positions):
    """Handle POST submission of application."""
    errors = []

    # Create or update application
    if existing_app and existing_app.status in ['draft', 'withdrawn']:
        application = existing_app
        application.status = 'draft'  # Reset if withdrawn
    else:
        application = SlatingApplication(
            period=period,
            applicant=user
        )

    # Process GPA
    gpa = request.POST.get('reported_gpa')
    if gpa:
        try:
            application.reported_gpa = float(gpa)
        except ValueError:
            errors.append("GPA must be a valid number")

    gpa_screenshot = request.FILES.get('gpa_screenshot')
    if gpa_screenshot:
        # Validate file
        allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf']
        if hasattr(gpa_screenshot, 'content_type') and gpa_screenshot.content_type not in allowed_types:
            errors.append("GPA screenshot must be an image or PDF")
        elif gpa_screenshot.size > 10 * 1024 * 1024:  # 10MB
            errors.append("GPA screenshot must be under 10MB")
        else:
            application.gpa_screenshot = gpa_screenshot

    # Process tiered position preferences
    first_choice = request.POST.getlist('positions_first_choice')
    second_choice = request.POST.getlist('positions_second_choice')
    third_choice = request.POST.getlist('positions_third_choice')
    do_not_want = request.POST.getlist('positions_do_not_want')

    application.position_preferences = {
        'first_choice': [int(p) for p in first_choice if p],
        'second_choice': [int(p) for p in second_choice if p],
        'third_choice': [int(p) for p in third_choice if p],
        'do_not_want': [int(p) for p in do_not_want if p],
    }

    # Validate required: at least one position in any wanted tier
    wanted_positions = (
        application.position_preferences['first_choice'] +
        application.position_preferences['second_choice'] +
        application.position_preferences['third_choice']
    )
    if not wanted_positions:
        errors.append("Please select at least one position you're interested in")

    # Collect field responses (validate before saving)
    field_responses = []
    for field in form_fields:
        # Skip special field types that are handled separately
        if field.field_type in ['gpa', 'position_preference']:
            continue

        value = None
        field_error = None

        # Get value based on field type
        if field.field_type in ['file', 'image']:
            value = request.FILES.get(f'field_{field.id}')
            if value:
                # Validate file
                if field.allowed_file_types and hasattr(value, 'content_type'):
                    if value.content_type not in field.allowed_file_types:
                        field_error = f"{field.label}: Invalid file type"
                if value.size > field.max_file_size_mb * 1024 * 1024:
                    field_error = f"{field.label}: File too large (max {field.max_file_size_mb}MB)"

        elif field.field_type in ['multiselect', 'checkbox']:
            value = request.POST.getlist(f'field_{field.id}')

        elif field.field_type in ['number']:
            raw = request.POST.get(f'field_{field.id}')
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    field_error = f"{field.label}: Must be a valid number"

        elif field.field_type == 'decimal':
            raw = request.POST.get(f'field_{field.id}')
            if raw:
                try:
                    value = float(raw)
                except ValueError:
                    field_error = f"{field.label}: Must be a valid number"

        else:
            value = request.POST.get(f'field_{field.id}', '').strip()

        # Validate required
        if field.is_required:
            if value is None or value == '' or (isinstance(value, list) and len(value) == 0):
                field_error = f"{field.label} is required"

        # Apply validation rules
        if value and field.validation_rules:
            for rule in field.validation_rules:
                if rule['type'] == 'min_length' and isinstance(value, str):
                    if len(value) < rule['value']:
                        field_error = f"{field.label}: Must be at least {rule['value']} characters"
                elif rule['type'] == 'max_length' and isinstance(value, str):
                    if len(value) > rule['value']:
                        field_error = f"{field.label}: Must be at most {rule['value']} characters"
                elif rule['type'] == 'min_value' and isinstance(value, (int, float)):
                    if value < rule['value']:
                        field_error = f"{field.label}: Must be at least {rule['value']}"
                elif rule['type'] == 'max_value' and isinstance(value, (int, float)):
                    if value > rule['value']:
                        field_error = f"{field.label}: Must be at most {rule['value']}"

        if field_error:
            errors.append(field_error)
        else:
            field_responses.append((field, value))

    # Check if this is a save draft or submit
    is_submit = request.POST.get('action') == 'submit'

    # For draft saves, don't require all fields
    if is_submit and errors:
        for error in errors:
            messages.error(request, error)

        # Re-render form with errors
        existing_responses = {}
        if application.pk:
            for response in application.responses.select_related('field').all():
                existing_responses[response.field_id] = response

        sections = {}
        for field in form_fields:
            section = field.section or 'General'
            if section not in sections:
                sections[section] = []
            sections[section].append(field)

        context = {
            'period': period,
            'fields': form_fields,  # Template uses 'fields' for regroup
            'form_fields': form_fields,
            'sections': sections,
            'positions': positions,
            'application': application,
            'responses': existing_responses,  # Template uses 'responses'
        }
        return render(request, 'slating/apply.html', context)

    # Save application
    if is_submit:
        application.status = 'submitted'
        application.submitted_at = timezone.now()
        # Calculate GPA level
        if application.reported_gpa:
            application.gpa_level = application.calculate_gpa_level()
    else:
        application.status = 'draft'

    application.save()

    # Save field responses
    for field, value in field_responses:
        response, _ = SlatingApplicationResponse.objects.get_or_create(
            application=application,
            field=field
        )

        # Clear previous values
        response.text_value = None
        response.number_value = None
        response.json_value = None
        # Don't clear file_value to preserve existing file if no new upload

        if field.field_type in ['file', 'image']:
            if value:
                response.file_value = value
        elif field.field_type in ['multiselect', 'checkbox']:
            response.json_value = value
        elif field.field_type in ['number', 'decimal', 'gpa']:
            response.number_value = value
        else:
            response.text_value = value if value else None

        response.save()

    if is_submit:
        messages.success(request, 'Your application has been submitted successfully!')

        # Log activity
        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='application_submitted',
            details=f'{user.name} submitted application',
            metadata={'application_id': application.id},
            ip_address=request.META.get('REMOTE_ADDR')
        )
    else:
        messages.success(request, 'Your application has been saved as draft.')

    return redirect('slating_my_applications')


@login_required
def my_applications(request):
    """
    View user's own applications.
    """
    applications = SlatingApplication.objects.filter(
        applicant=request.user
    ).select_related('period').order_by('-created_at')

    context = {
        'applications': applications,
    }

    return render(request, 'slating/my_applications.html', context)


@login_required
def withdraw_application(request, app_id):
    """
    Withdraw an application.
    """
    if request.method != 'POST':
        return redirect('slating_my_applications')

    application = get_object_or_404(
        SlatingApplication,
        id=app_id,
        applicant=request.user
    )

    # Can only withdraw submitted applications
    if application.status not in ['draft', 'submitted', 'under_review']:
        messages.error(request, 'This application cannot be withdrawn.')
        return redirect('slating_my_applications')

    application.status = 'withdrawn'
    application.save(update_fields=['status'])

    # Log activity
    SlatingActivity.objects.create(
        period=application.period,
        user=request.user,
        action='application_submitted',  # Using existing action type
        details=f'{request.user.name} withdrew application',
        metadata={'application_id': application.id, 'action': 'withdrawn'},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, 'Your application has been withdrawn.')
    return redirect('slating_my_applications')


@login_required
def view_application(request, app_id):
    """
    View a single application (for applicant viewing their own).
    """
    application = get_object_or_404(
        SlatingApplication,
        id=app_id,
        applicant=request.user
    )

    # Get responses
    responses = application.responses.select_related('field').all()
    response_dict = {r.field_id: r for r in responses}

    # Get fields
    fields = application.period.form_fields.filter(is_active=True).order_by('display_order')

    # Get positions
    positions = application.period.positions.filter(is_active=True)
    position_dict = {p.id: p for p in positions}

    # Build tiered position display
    prefs = application.position_preferences or {}

    # Handle legacy list format
    if isinstance(prefs, list):
        prefs = {
            'first_choice': prefs,
            'second_choice': [],
            'third_choice': [],
            'do_not_want': [],
        }

    tiered_positions = {
        'first_choice': [position_dict.get(pid) for pid in prefs.get('first_choice', []) if pid in position_dict],
        'second_choice': [position_dict.get(pid) for pid in prefs.get('second_choice', []) if pid in position_dict],
        'third_choice': [position_dict.get(pid) for pid in prefs.get('third_choice', []) if pid in position_dict],
        'do_not_want': [position_dict.get(pid) for pid in prefs.get('do_not_want', []) if pid in position_dict],
    }

    # For backwards compatibility, also provide flat list of wanted positions
    selected_positions = (
        tiered_positions['first_choice'] +
        tiered_positions['second_choice'] +
        tiered_positions['third_choice']
    )

    context = {
        'application': application,
        'period': application.period,
        'fields': fields,
        'responses': response_dict,
        'selected_positions': selected_positions,
        'tiered_positions': tiered_positions,
    }

    return render(request, 'slating/view_application.html', context)
