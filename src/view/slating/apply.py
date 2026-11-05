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
from src.decorators import pledge_page_allowed
from src.utils.file_validation import validate_uploaded_file


@login_required
@pledge_page_allowed('slating_apply')
def apply_view(request, period_id):
    """
    Dynamic application form submission.
    Form fields are defined by SlatingFormField model.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    existing_app, refusal = apply_refusal(period, user)
    if refusal:
        kind, message = refusal
        if kind == 'already_applied':
            messages.info(request, message)
            return redirect('slating_my_applications')
        messages.error(request, message)
        return redirect('slating_dashboard')

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

    sections = field_sections(form_fields)

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


APPLY_CLOSED = 'Applications are not currently open for this period.'
ALREADY_APPLIED = 'You have already submitted an application.'


def apply_refusal(period, user):
    """
    ``(existing application, refusal)`` for ``user`` opening ``period``'s
    application form; ``refusal`` is ``(kind, message)`` or None. The web page
    and the app's form (``src/mobile/views_slating_apply.py``) check the same
    things in the same order: applications open, the member may apply
    (v4.0.0), and no application already past draft/withdrawn.
    """
    if not period.can_apply():
        return None, ('closed', APPLY_CLOSED)

    # v4.0.0: only active members (not inactive, alumni or advisors).
    from .permissions import COMMITTEE_CANT_RUN, applicant_ineligibility, on_slating_committee
    reason = applicant_ineligibility(user)
    if reason:
        return None, ('ineligible', reason)
    # 11-05-26 (the chapter's rule): no candidate on the slating committee.
    if on_slating_committee(user, period):
        return None, ('committee_member', COMMITTEE_CANT_RUN)

    existing_app = SlatingApplication.objects.filter(period=period, applicant=user).first()
    if existing_app and existing_app.status not in ['draft', 'withdrawn']:
        return existing_app, ('already_applied', ALREADY_APPLIED)
    return existing_app, None


def field_sections(form_fields):
    """The form's fields grouped by section (``General`` when blank), in order."""
    sections = {}
    for field in form_fields:
        section = field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(field)
    return sections


def save_application(period, user, existing_app, form_fields, positions, post, files, is_submit):
    """
    Save an application from posted values: the website's form, or the app's
    (``post`` has ``get``/``getlist`` like a QueryDict, ``files`` has ``get``).

    Returns ``(application, errors)``. A draft saves whatever validated and
    drops the rest; a submit with errors saves nothing (the caller shows the
    errors with the unsaved ``application``).
    """
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
    gpa = post.get('reported_gpa')
    if gpa:
        # 11-05-26: a real GPA from 0 to 5 (nan / inf / huge values were a 500
        # on save, negative ones were stored).
        import math
        try:
            value = float(gpa)
        except (ValueError, TypeError):
            value = None
        if value is None or not math.isfinite(value) or not 0 <= value <= 5:
            errors.append("GPA must be a number from 0 to 5")
        else:
            application.reported_gpa = round(value, 3)

    gpa_screenshot = files.get('gpa_screenshot')
    if gpa_screenshot:
        # ⚠️ v3.19.7 — THIS USED TO VALIDATE THE UPLOADER'S OWN CLAIM.
        # The check was `gpa_screenshot.content_type not in [image/*, pdf]`, and
        # `UploadedFile.content_type` is the `Content-Type` header of the
        # multipart part — sent by the client, never measured. A file named
        # `transcript.html` declaring `image/png` passed, was stored as
        # `slating/gpa_screenshots/<uuid>.html`, and was served back to the
        # committee as `text/html` from this origin. The `hasattr` guard made it
        # weaker still: a part with no declared type skipped the check entirely.
        #
        # `validate_uploaded_file` measures the content with python-magic and
        # requires it to agree with the extension. **Replacing rather than
        # adding to the old check is deliberate — a header the uploader controls
        # is not a second opinion, and leaving it in would suggest it was one.**
        try:
            validate_uploaded_file(gpa_screenshot)
            application.gpa_screenshot = gpa_screenshot
        except ValidationError as exc:
            errors.append(f"GPA screenshot: {'; '.join(exc.messages)}")

    # Process tiered position preferences
    first_choice = post.getlist('positions_first_choice')
    second_choice = post.getlist('positions_second_choice')
    third_choice = post.getlist('positions_third_choice')
    do_not_want = post.getlist('positions_do_not_want')

    # v4.0.0: was `int(p)` on whatever was posted — junk was a 500, and any
    # position id (another period's) was stored. Only this period's active
    # positions, each in its first tier only.
    valid_ids = {p.id for p in positions}
    seen = set()

    def _tier(values):
        out = []
        for v in values:
            pid = int(v) if str(v).isdigit() else None
            if pid in valid_ids and pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out

    application.position_preferences = {
        'first_choice': _tier(first_choice),
        'second_choice': _tier(second_choice),
        'third_choice': _tier(third_choice),
        'do_not_want': _tier(do_not_want),
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
            value = files.get(f'field_{field.id}')
            if value:
                # v3.19.7 — measured validation first (see the GPA screenshot
                # above for why the declared `content_type` is not evidence),
                # then the per-field limits the form author configured. Both:
                # `allowed_file_types` is a narrowing the chapter chose and
                # `validate_uploaded_file` is the floor nobody may drop below.
                try:
                    validate_uploaded_file(value)
                except ValidationError as exc:
                    field_error = f"{field.label}: {'; '.join(exc.messages)}"
                if field.allowed_file_types and hasattr(value, 'content_type'):
                    if value.content_type not in field.allowed_file_types:
                        field_error = f"{field.label}: Invalid file type"
                if value.size > field.max_file_size_mb * 1024 * 1024:
                    field_error = f"{field.label}: File too large (max {field.max_file_size_mb}MB)"

        elif field.field_type in ['multiselect', 'checkbox']:
            value = post.getlist(f'field_{field.id}')

        elif field.field_type in ['number']:
            raw = post.get(f'field_{field.id}')
            if raw:
                try:
                    value = int(raw)
                except ValueError:
                    field_error = f"{field.label}: Must be a valid number"

        elif field.field_type == 'decimal':
            raw = post.get(f'field_{field.id}')
            if raw:
                try:
                    value = float(raw)
                except ValueError:
                    field_error = f"{field.label}: Must be a valid number"

        else:
            value = post.get(f'field_{field.id}', '').strip()

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

    if is_submit and errors:
        return application, errors

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
        # Log activity
        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='application_submitted',
            details=f'{user.name} submitted application',
            metadata={'application_id': application.id}
        )

    return application, errors


def _handle_application_submit(request, period, user, existing_app, form_fields, positions):
    """Handle POST submission of application."""
    # Check if this is a save draft or submit
    is_submit = request.POST.get('action') == 'submit'
    application, errors = save_application(period, user, existing_app, form_fields, positions,
                                           request.POST, request.FILES, is_submit)

    # For draft saves, don't require all fields
    if is_submit and errors:
        for error in errors:
            messages.error(request, error)

        # Re-render form with errors
        existing_responses = {}
        if application.pk:
            for response in application.responses.select_related('field').all():
                existing_responses[response.field_id] = response

        context = {
            'period': period,
            'fields': form_fields,  # Template uses 'fields' for regroup
            'form_fields': form_fields,
            'sections': field_sections(form_fields),
            'positions': positions,
            'application': application,
            'responses': existing_responses,  # Template uses 'responses'
        }
        return render(request, 'slating/apply.html', context)

    if is_submit:
        messages.success(request, 'Your application has been submitted successfully!')
    else:
        messages.success(request, 'Your application has been saved as draft.')

    return redirect('slating_my_applications')


@login_required
def my_applications(request):
    """
    View user's own applications.
    """
    # v3.17.3: my_applications.html:76 checks `app.interviews` per row.
    applications = SlatingApplication.objects.filter(
        applicant=request.user
    ).select_related('period').prefetch_related('interviews').order_by('-created_at')

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
        metadata={'application_id': application.id, 'action': 'withdrawn'}
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

    tiered_positions, selected_positions = tiered_position_lists(application, position_dict)

    context = {
        'application': application,
        'period': application.period,
        'fields': fields,
        'responses': response_dict,
        'selected_positions': selected_positions,
        'tiered_positions': tiered_positions,
    }

    return render(request, 'slating/view_application.html', context)


TIERS = ('first_choice', 'second_choice', 'third_choice', 'do_not_want')


def tiered_position_lists(application, position_dict):
    """
    ``(tiered, wanted)``: the application's positions per preference tier
    (``{position id: position}`` lookups, unknown ids dropped; a legacy plain
    list is all first choice), and the wanted ones in order (no do-not-want).
    """
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
        tier: [position_dict.get(pid) for pid in prefs.get(tier, []) if pid in position_dict]
        for tier in TIERS
    }

    # For backwards compatibility, also provide flat list of wanted positions
    selected_positions = (
        tiered_positions['first_choice'] +
        tiered_positions['second_choice'] +
        tiered_positions['third_choice']
    )
    return tiered_positions, selected_positions
