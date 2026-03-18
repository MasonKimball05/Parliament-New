"""
Officer views for role management: list, add, edit, delete roles.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods

from src.models import Role, ParliamentUser, ActivityLog
from src.decorators import admin_required

logger = logging.getLogger(__name__)


@login_required
@admin_required
def manage_roles(request):
    """Display all roles with management options."""
    roles = Role.objects.all().order_by('name')

    # Add holder count for each role
    roles_data = []
    for role in roles:
        holder_count = ParliamentUser.objects.filter(roles=role, member_status='Active').count()
        roles_data.append({
            'role': role,
            'holder_count': holder_count,
            'holders': list(ParliamentUser.objects.filter(roles=role, member_status='Active').values_list('name', flat=True)[:5]),
        })

    context = {
        'roles_data': roles_data,
        'total_roles': roles.count(),
    }

    return render(request, 'officer/manage_roles.html', context)


@login_required
@admin_required
@require_http_methods(['GET', 'POST'])
def role_detail(request, role_id):
    """Get or update a role's details."""
    role = get_object_or_404(Role, id=role_id)

    if request.method == 'GET':
        holders = ParliamentUser.objects.filter(roles=role, member_status='Active').values('user_id', 'name')
        return JsonResponse({
            'success': True,
            'role': {
                'id': role.id,
                'name': role.name,
                'code': role.code,
                'description': role.description,
                'one_per_chapter': role.one_per_chapter,
                'grants_admin': role.grants_admin,
                'holders': list(holders),
            }
        })

    # POST - Update role
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    # Track changes
    changes = []

    if 'name' in data and data['name'] != role.name:
        # Check for duplicate name
        if Role.objects.filter(name=data['name']).exclude(id=role.id).exists():
            return JsonResponse({'success': False, 'error': 'A role with this name already exists.'}, status=400)
        changes.append(f"name: {role.name} -> {data['name']}")
        role.name = data['name']

    if 'code' in data and data['code'] != role.code:
        new_code = data['code'].upper().strip()
        # Check for duplicate code
        if Role.objects.filter(code__iexact=new_code).exclude(id=role.id).exists():
            return JsonResponse({'success': False, 'error': 'A role with this code already exists.'}, status=400)
        changes.append(f"code: {role.code} -> {new_code}")
        role.code = new_code

    if 'description' in data:
        if data['description'] != role.description:
            changes.append(f"description updated")
            role.description = data['description']

    if 'one_per_chapter' in data:
        new_val = bool(data['one_per_chapter'])
        if new_val != role.one_per_chapter:
            changes.append(f"one_per_chapter: {role.one_per_chapter} -> {new_val}")
            role.one_per_chapter = new_val

    if 'grants_admin' in data:
        new_val = bool(data['grants_admin'])
        if new_val != role.grants_admin:
            changes.append(f"grants_admin: {role.grants_admin} -> {new_val}")
            role.grants_admin = new_val

    role.save()

    # Log the activity
    if changes:
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.get_display_name()} updated role {role.name}: {", ".join(changes)}',
            request=request,
            metadata={
                'action': 'edit_role',
                'role_id': role.id,
                'role_name': role.name,
                'changes': changes,
            }
        )
        logger.info(f"Admin {request.user.user_id} updated role {role.id}: {changes}")

    return JsonResponse({
        'success': True,
        'message': f'Role "{role.name}" updated successfully.',
        'changes': changes,
    })


@login_required
@admin_required
@require_POST
def add_role(request):
    """Create a new role."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    name = data.get('name', '').strip()
    code = data.get('code', '').upper().strip()
    description = data.get('description', '').strip()
    one_per_chapter = bool(data.get('one_per_chapter', False))
    grants_admin = bool(data.get('grants_admin', False))

    if not name:
        return JsonResponse({'success': False, 'error': 'Role name is required.'}, status=400)

    if not code:
        return JsonResponse({'success': False, 'error': 'Role code is required.'}, status=400)

    # Check for duplicates
    if Role.objects.filter(name=name).exists():
        return JsonResponse({'success': False, 'error': 'A role with this name already exists.'}, status=400)

    if Role.objects.filter(code__iexact=code).exists():
        return JsonResponse({'success': False, 'error': 'A role with this code already exists.'}, status=400)

    role = Role.objects.create(
        name=name,
        code=code,
        description=description,
        one_per_chapter=one_per_chapter,
        grants_admin=grants_admin,
    )

    # Log the activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} created role {role.name} ({role.code})',
        request=request,
        metadata={
            'action': 'add_role',
            'role_id': role.id,
            'role_name': role.name,
            'role_code': role.code,
        }
    )
    logger.info(f"Admin {request.user.user_id} created role {role.id}: {role.name}")

    return JsonResponse({
        'success': True,
        'message': f'Role "{role.name}" created successfully.',
        'role': {
            'id': role.id,
            'name': role.name,
            'code': role.code,
        }
    })


@login_required
@admin_required
@require_POST
def delete_role(request, role_id):
    """Delete a role."""
    role = get_object_or_404(Role, id=role_id)

    # Check if role has any holders
    holder_count = ParliamentUser.objects.filter(roles=role).count()
    if holder_count > 0:
        return JsonResponse({
            'success': False,
            'error': f'Cannot delete role "{role.name}" - it is currently assigned to {holder_count} member(s). Please remove all members from this role first.'
        }, status=400)

    role_name = role.name
    role_code = role.code
    role_id_val = role.id

    role.delete()

    # Log the activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} deleted role {role_name} ({role_code})',
        request=request,
        metadata={
            'action': 'delete_role',
            'role_id': role_id_val,
            'role_name': role_name,
            'role_code': role_code,
        }
    )
    logger.info(f"Admin {request.user.user_id} deleted role {role_id_val}: {role_name}")

    return JsonResponse({
        'success': True,
        'message': f'Role "{role_name}" deleted successfully.',
    })
