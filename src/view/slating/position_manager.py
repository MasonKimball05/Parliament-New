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


@login_required
@slating_chair_required
def manage_positions(request, period_id):
    """
    View and manage positions for a slating period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    positions = period.positions.all().order_by('display_order', 'title')
    active_positions = positions.filter(is_active=True)
    inactive_positions = positions.filter(is_active=False)

    # Get available roles for linking
    roles = Role.objects.all().order_by('name')

    context = {
        'period': period,
        'positions': active_positions,
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

    title = request.POST.get('title', '').strip()
    code = request.POST.get('code', '').strip().upper()

    if not title or not code:
        messages.error(request, 'Title and code are required.')
        return redirect('slating_positions', period_id=period_id)

    # Check for duplicate code
    if period.positions.filter(code=code).exists():
        messages.error(request, f'A position with code "{code}" already exists.')
        return redirect('slating_positions', period_id=period_id)

    # Build position data
    position_data = {
        'title': title,
        'code': code,
        'description': request.POST.get('description', ''),
        'requires_prior_experience': request.POST.get('requires_prior_experience') == 'on',
        'display_order': period.positions.count(),
    }

    # Link to role if specified
    role_id = request.POST.get('role')
    if role_id:
        try:
            position_data['role'] = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            pass

    # GPA requirement
    min_gpa = request.POST.get('min_gpa')
    if min_gpa:
        try:
            position_data['min_gpa'] = float(min_gpa)
        except ValueError:
            pass

    # Semesters requirement
    min_semesters = request.POST.get('min_semesters_active')
    if min_semesters:
        try:
            position_data['min_semesters_active'] = int(min_semesters)
        except ValueError:
            pass

    # Eligibility restrictions
    eligible_types = request.POST.getlist('eligible_member_types')
    if eligible_types:
        position_data['eligible_member_types'] = eligible_types

    eligible_years = request.POST.getlist('eligible_class_years')
    if eligible_years:
        position_data['eligible_class_years'] = eligible_years

    position_data['allow_abstain'] = request.POST.get('allow_abstain') != 'off'

    # Create position
    position = SlatingPosition.objects.create(period=period, **position_data)

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='position_added',
        details=f'Added position: {title} ({code})',
        metadata={'position_id': position.id},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Position "{title}" added.')
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
        data = {
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
        return JsonResponse(data)

    # POST - update position
    position.title = request.POST.get('title', position.title).strip()
    position.description = request.POST.get('description', '')
    position.requires_prior_experience = request.POST.get('requires_prior_experience') == 'on'

    # Note: code is not editable after creation

    # Link to role
    role_id = request.POST.get('role')
    if role_id:
        try:
            position.role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            position.role = None
    else:
        position.role = None

    # GPA requirement
    min_gpa = request.POST.get('min_gpa')
    if min_gpa:
        try:
            position.min_gpa = float(min_gpa)
        except ValueError:
            position.min_gpa = None
    else:
        position.min_gpa = None

    # Semesters requirement
    min_semesters = request.POST.get('min_semesters_active')
    if min_semesters:
        try:
            position.min_semesters_active = int(min_semesters)
        except ValueError:
            pass

    # Eligibility restrictions
    position.eligible_member_types = request.POST.getlist('eligible_member_types')
    position.eligible_class_years = request.POST.getlist('eligible_class_years')
    position.allow_abstain = request.POST.get('allow_abstain') != 'off'

    position.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='position_modified',
        details=f'Updated position: {position.title}',
        metadata={'position_id': position.id},
        ip_address=request.META.get('REMOTE_ADDR')
    )

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

    action = request.POST.get('action', 'deactivate')

    if action == 'hard_delete' and request.user.is_admin:
        # Hard delete - only for admins and if no applications reference it
        if position.slate_assignments.exists():
            messages.error(request, 'Cannot delete position that has slate assignments.')
            return redirect('slating_positions', period_id=period_id)

        title = position.title
        position.delete()
        messages.success(request, f'Position "{title}" permanently deleted.')
    else:
        # Soft delete
        position.is_active = False
        position.save()
        messages.success(request, f'Position "{position.title}" deactivated.')

    return redirect('slating_positions', period_id=period_id)


@login_required
@slating_chair_required
def reorder_positions(request, period_id):
    """
    Reorder positions via AJAX.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    try:
        import json
        period = get_object_or_404(SlatingPeriod, id=period_id)
        order_data = json.loads(request.POST.get('order', '[]'))

        for idx, position_id in enumerate(order_data):
            SlatingPosition.objects.filter(id=position_id, period=period).update(display_order=idx)

        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
@slating_chair_required
def copy_default_positions(request, period_id):
    """
    Copy default positions from Role model to this period.
    """
    if request.method != 'POST':
        return redirect('slating_positions', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Get existing position codes
    existing_codes = set(period.positions.values_list('code', flat=True))

    # Add default roles as positions
    added = 0
    for role_id, code, name in Role.DEFAULT_ROLES:
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
        messages.success(request, f'Added {added} default positions.')

        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='position_added',
            details=f'Added {added} default positions',
            ip_address=request.META.get('REMOTE_ADDR')
        )
    else:
        messages.info(request, 'All default positions already exist.')

    return redirect('slating_positions', period_id=period_id)
