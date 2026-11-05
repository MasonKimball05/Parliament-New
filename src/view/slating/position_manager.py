"""
Slating Position Manager Views

Manage positions for a slating period.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from src.models import SlatingPeriod, SlatingPosition, SlatingActivity, Role
from .permissions import slating_chair_required


# ── Shared with the app (src/mobile/views_slating_setup.py, 11-04-26) ────────

class PositionError(Exception):
    """A refusal for the chair: ``code`` for the app and the web's message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def period_positions(period):
    """(active, inactive) positions, in slate order."""
    positions = period.positions.all().order_by('display_order', 'title')
    return positions.filter(is_active=True), positions.filter(is_active=False)


def position_data(position):
    """A position's fields, as the web's edit modal reads them (``GET …/edit/``)."""
    return {
        'id': position.id,
        'title': position.title,
        'code': position.code,
        'description': position.description,
        'role_id': position.role_id,
        'min_gpa': str(position.min_gpa) if position.min_gpa else '',
        'min_semesters_active': position.min_semesters_active,
        'requires_prior_experience': position.requires_prior_experience,
        'eligible_member_types': position.eligible_member_types,
        'eligible_class_years': position.eligible_class_years,
        'is_active': position.is_active,
        'allow_abstain': position.allow_abstain,
    }


def create_position(period, user, data):
    """
    Add a position from the form's fields (``data.get`` / ``getlist``): a
    title and a code (upper-cased, unique in the period) are required; it
    goes last; logged. Raises PositionError.
    """
    title = data.get('title', '').strip()
    code = data.get('code', '').strip().upper()

    if not title or not code:
        raise PositionError('invalid_input', 'Title and code are required.')

    # Check for duplicate code
    if period.positions.filter(code=code).exists():
        raise PositionError('duplicate_code', f'A position with code "{code}" already exists.')

    # Build position data
    fields = {
        'title': title,
        'code': code,
        'description': data.get('description', ''),
        'requires_prior_experience': data.get('requires_prior_experience') == 'on',
        'display_order': period.positions.count(),
    }

    # Link to role if specified
    role_id = data.get('role')
    if role_id:
        try:
            fields['role'] = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            pass

    # GPA requirement
    min_gpa = data.get('min_gpa')
    if min_gpa:
        try:
            fields['min_gpa'] = float(min_gpa)
        except ValueError:
            pass

    # Semesters requirement
    min_semesters = data.get('min_semesters_active')
    if min_semesters:
        try:
            fields['min_semesters_active'] = int(min_semesters)
        except ValueError:
            pass

    # Eligibility restrictions
    eligible_types = data.getlist('eligible_member_types')
    if eligible_types:
        fields['eligible_member_types'] = eligible_types

    eligible_years = data.getlist('eligible_class_years')
    if eligible_years:
        fields['eligible_class_years'] = eligible_years

    fields['allow_abstain'] = data.get('allow_abstain') != 'off'

    # Create position
    position = SlatingPosition.objects.create(period=period, **fields)

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='position_added',
        details=f'Added position: {title} ({code})',
        metadata={'position_id': position.id}
    )
    return position


def update_position(period, position, user, data):
    """
    Save the edit form (the code can't change). Every field is the form's: a
    checkbox left out is off, no role or GPA clears it — except abstain, on
    unless ``allow_abstain`` is ``off``; logged.
    """
    position.title = data.get('title', position.title).strip()
    position.description = data.get('description', '')
    position.requires_prior_experience = data.get('requires_prior_experience') == 'on'

    # Note: code is not editable after creation

    # Link to role
    role_id = data.get('role')
    if role_id:
        try:
            position.role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            position.role = None
    else:
        position.role = None

    # GPA requirement
    min_gpa = data.get('min_gpa')
    if min_gpa:
        try:
            position.min_gpa = float(min_gpa)
        except ValueError:
            position.min_gpa = None
    else:
        position.min_gpa = None

    # Semesters requirement
    min_semesters = data.get('min_semesters_active')
    if min_semesters:
        try:
            position.min_semesters_active = int(min_semesters)
        except ValueError:
            pass

    # Eligibility restrictions
    position.eligible_member_types = data.getlist('eligible_member_types')
    position.eligible_class_years = data.getlist('eligible_class_years')
    position.allow_abstain = data.get('allow_abstain') != 'off'

    position.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='position_modified',
        details=f'Updated position: {position.title}',
        metadata={'position_id': position.id}
    )
    return position


def remove_position(position, user, action='deactivate'):
    """
    Delete: deactivate it, or for a site admin with ``hard_delete`` remove it
    for good (not while a slate has it). Returns the web's message; raises
    PositionError.
    """
    if action == 'hard_delete' and user.is_admin:
        # Hard delete - only for admins and if no applications reference it
        if position.slate_assignments.exists():
            raise PositionError('has_slate', 'Cannot delete position that has slate assignments.')

        title = position.title
        position.delete()
        return f'Position "{title}" permanently deleted.'
    # Soft delete
    position.is_active = False
    position.save()
    return f'Position "{position.title}" deactivated.'


def reorder_period_positions(period, position_order):
    """Slate order: each listed position (of this period) takes its index."""
    for index, position_id in enumerate(position_order):
        SlatingPosition.objects.filter(
            id=position_id,
            period=period
        ).update(display_order=index)


def toggle_position(position):
    """Active ↔ inactive. Returns the new state."""
    position.is_active = not position.is_active
    position.save()
    return position.is_active


def copy_default_positions_into(period, user):
    """Add the default slate's positions the period doesn't have yet (by code); logged. Returns how many."""
    # Get existing position codes
    existing_codes = set(period.positions.values_list('code', flat=True))

    # Add default roles as positions
    added = 0
    for role_id, code, name in default_slate_positions():
        if code not in existing_codes:
            try:
                role = Role.objects.get(code=code)
            except Role.DoesNotExist:
                role = None

            SlatingPosition.objects.create(
                period=period,
                role=role,
                title=name,
                code=code,
                display_order=role_id,
            )
            added += 1

    if added > 0:
        SlatingActivity.objects.create(
            period=period,
            user=user,
            action='position_added',
            details=f'Added {added} default positions'
        )
    return added


@login_required
@slating_chair_required
def manage_positions(request, period_id):
    """
    View and manage positions for a slating period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    active_positions, inactive_positions = period_positions(period)

    # Get available roles for linking
    roles = Role.objects.all().order_by('name')

    context = {
        'period': period,
        'positions': active_positions.select_related('role'),   # the list shows each one's role
        'inactive_positions': inactive_positions,
        'roles': roles,
    }

    return render(request, 'slating/positions.html', context)


@login_required
@slating_chair_required
def add_position(request, period_id):
    """
    Add a new position to the slating period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    if request.method != 'POST':
        return redirect('slating_positions', period_id=period_id)

    try:
        position = create_position(period, request.user, request.POST)
    except PositionError as e:
        messages.error(request, str(e))
        return redirect('slating_positions', period_id=period_id)

    messages.success(request, f'Position "{position.title}" added.')
    return redirect('slating_positions', period_id=period_id)


@login_required
@slating_chair_required
def edit_position(request, period_id, position_id):
    """
    Edit an existing position.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    position = get_object_or_404(SlatingPosition, id=position_id, period=period)

    if request.method == 'GET':
        # Return JSON for AJAX modal
        return JsonResponse(position_data(position))

    # POST - update position. The edit form shows only the title, description,
    # GPA and abstain; keep the fields it doesn't show as they are, rather than
    # clearing the linked role and eligibility on every save.
    data = request.POST.copy()
    if 'role' not in data:
        data['role'] = str(position.role_id or '')
    if 'requires_prior_experience' not in data and position.requires_prior_experience:
        data['requires_prior_experience'] = 'on'
    for key in ('eligible_member_types', 'eligible_class_years'):
        if key not in data:
            data.setlist(key, list(getattr(position, key) or []))
    update_position(period, position, request.user, data)

    messages.success(request, f'Position "{position.title}" updated.')
    return redirect('slating_positions', period_id=period_id)


@login_required
@slating_chair_required
def delete_position(request, period_id, position_id):
    """
    Delete (deactivate) a position.
    """
    if request.method != 'POST':
        return redirect('slating_positions', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    position = get_object_or_404(SlatingPosition, id=position_id, period=period)

    try:
        messages.success(request, remove_position(position, request.user, request.POST.get('action', 'deactivate')))
    except PositionError as e:
        messages.error(request, str(e))

    return redirect('slating_positions', period_id=period_id)


#: Roles that are NOT slated (v4.0.0, Mason 10-26-26): Vice President of
#: Administration, Vice President of Risk Management and the Constitution &
#: Bylaws Chair. They stay roles (Role.DEFAULT_ROLES also seeds the chapter's
#: roles); they're just not on the default slate.
SLATE_EXCLUDED_ROLE_CODES = frozenset({'VPA', 'VPRM', 'CNB'})


def default_slate_positions():
    """(order, code, title) for "+ Default Positions", one list for the button and its modal."""
    return [r for r in Role.DEFAULT_ROLES if r[1] not in SLATE_EXCLUDED_ROLE_CODES]


@login_required
@slating_chair_required
def copy_default_positions(request, period_id):
    """
    Copy default positions from Role model to this period.
    """
    if request.method != 'POST':
        return redirect('slating_positions', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    added = copy_default_positions_into(period, request.user)
    if added > 0:
        messages.success(request, f'Added {added} default positions.')
    else:
        messages.info(request, 'All default positions already exist.')

    return redirect('slating_positions', period_id=period_id)
