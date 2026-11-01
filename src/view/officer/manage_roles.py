"""
Officer views for role management: list, add, edit, delete roles.

The rules and side effects live in the helpers below (``holders_by_role``,
``create_role``, ``update_role``, ``delete_role_checked``, ``assign_role``,
``unassign_role``, ``assignable_members``), which the app's role screens
(src/mobile/views_officer_roles.py) call too. Each raises ``RoleChangeRefused``
with the message and status the page answers with.
"""
import json
import logging

from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods

from src.models import Role, ParliamentUser, ActivityLog
from src.decorators import officer_required

logger = logging.getLogger(__name__)

#: v3.51.0 — officers only (officer_required lets chairs in). A chair could
#: otherwise rename the code of a role he holds to one that carries
#: permissions (e.g. 'CNB'). See src.permissions.role_change_error.
ROLE_EDIT_REFUSED = 'Only officers can create, change or delete roles.'


class RoleChangeRefused(Exception):
    """Why a role change was refused, and the HTTP status the page gives it."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _refused_response(exc):
    return JsonResponse({'success': False, 'error': exc.message}, status=exc.status)


def check_can_edit_roles(user):
    """Raise unless `user` may create, change or delete role definitions."""
    if not user.is_officer:
        raise RoleChangeRefused(ROLE_EDIT_REFUSED, status=403)


def holders_by_role(roles):
    """
    {role id: [(user_id, name), ...]} for the active holders of `roles`, in
    one pass over the role/holder pairs.

    v3.17.4: was two queries per role — a COUNT and a names slice — so 13
    queries for six roles. Ordered by user_id so the five the page shows are
    the same five as before (`values_list(...)[:5]` inherited
    ParliamentUser's Meta ordering on user_id; sorting explicitly here makes
    the choice deliberate rather than incidental).
    """
    out = {}
    for role_id, user_id, name in (
        ParliamentUser.objects
        .filter(roles__in=roles, member_status='Active')
        .order_by('user_id')
        .values_list('roles__id', 'user_id', 'name')
    ):
        out.setdefault(role_id, []).append((user_id, name))
    return out


def _text(data, key):
    value = data.get(key)
    return '' if value is None else str(value)


def _check_lengths(name, code):
    if len(name) > Role._meta.get_field('name').max_length:
        raise RoleChangeRefused('Role name is too long.')
    if len(code) > Role._meta.get_field('code').max_length:
        raise RoleChangeRefused('Role code is too long.')


def create_role(request, data):
    """Create a role from `data` (name, code, description, one_per_chapter, grants_admin) and log it."""
    check_can_edit_roles(request.user)
    name = _text(data, 'name').strip()
    code = _text(data, 'code').upper().strip()
    description = _text(data, 'description').strip()
    one_per_chapter = bool(data.get('one_per_chapter', False))
    # Only admins may create roles that grant admin privileges
    grants_admin = bool(data.get('grants_admin', False)) if request.user.is_admin else False

    if not name:
        raise RoleChangeRefused('Role name is required.')
    if not code:
        raise RoleChangeRefused('Role code is required.')
    _check_lengths(name, code)

    # Check for duplicates
    if Role.objects.filter(name=name).exists():
        raise RoleChangeRefused('A role with this name already exists.')
    if Role.objects.filter(code__iexact=code).exists():
        raise RoleChangeRefused('A role with this code already exists.')

    role = Role.objects.create(
        name=name,
        code=code,
        description=description,
        one_per_chapter=one_per_chapter,
        grants_admin=grants_admin,
    )

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
    logger.info(f"User {request.user.user_id} created role {role.id}: {role.name}")
    return role


def update_role(request, role, data):
    """Apply the keys present in `data` to `role`, log what changed, and return the change list."""
    check_can_edit_roles(request.user)

    # Track changes
    changes = []

    if 'name' in data and data['name'] != role.name:
        new_name = _text(data, 'name').strip()
        if not new_name:
            raise RoleChangeRefused('Role name is required.')
        _check_lengths(new_name, '')
        # Check for duplicate name
        if Role.objects.filter(name=new_name).exclude(id=role.id).exists():
            raise RoleChangeRefused('A role with this name already exists.')
        if new_name != role.name:
            changes.append(f"name: {role.name} -> {new_name}")
            role.name = new_name

    if 'code' in data and data['code'] != role.code:
        new_code = _text(data, 'code').upper().strip()
        if not new_code:
            raise RoleChangeRefused('Role code is required.')
        _check_lengths('', new_code)
        # Check for duplicate code
        if Role.objects.filter(code__iexact=new_code).exclude(id=role.id).exists():
            raise RoleChangeRefused('A role with this code already exists.')
        if new_code != role.code:
            changes.append(f"code: {role.code} -> {new_code}")
            role.code = new_code

    if 'description' in data:
        new_description = _text(data, 'description')
        if new_description != role.description:
            changes.append("description updated")
            role.description = new_description

    if 'one_per_chapter' in data:
        new_val = bool(data['one_per_chapter'])
        if new_val != role.one_per_chapter:
            changes.append(f"one_per_chapter: {role.one_per_chapter} -> {new_val}")
            role.one_per_chapter = new_val

    if 'grants_admin' in data and request.user.is_admin:
        new_val = bool(data['grants_admin'])
        if new_val != role.grants_admin:
            changes.append(f"grants_admin: {role.grants_admin} -> {new_val}")
            role.grants_admin = new_val

    role.save()

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
        logger.info(f"User {request.user.user_id} updated role {role.id}: {changes}")
    return changes


def delete_role_checked(request, role):
    """Delete `role` if nobody holds it, and log it. Returns the deleted role's name."""
    check_can_edit_roles(request.user)

    # Check if role has any holders
    holder_count = ParliamentUser.objects.filter(roles=role).count()
    if holder_count > 0:
        raise RoleChangeRefused(
            f'Cannot delete role "{role.name}" - it is currently assigned to {holder_count} member(s). '
            'Please remove all members from this role first.'
        )

    role_name = role.name
    role_code = role.code
    role_id_val = role.id

    role.delete()

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
    logger.info(f"User {request.user.user_id} deleted role {role_id_val}: {role_name}")
    return role_name


def assign_role(request, role, user):
    """Give `role` to `user` (role_change_error's rules; one holder for an exclusive role) and log it."""
    from src.permissions import role_change_error  # v3.50.1
    error = role_change_error(request.user, role, user)
    if error:
        raise RoleChangeRefused(error, status=403)

    if role.one_per_chapter and ParliamentUser.objects.filter(roles=role).exclude(user_id=user.user_id).exists():
        raise RoleChangeRefused(f'"{role.name}" can only have one holder. Remove the current holder first.')

    user.roles.add(role)

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} assigned {user.name} to role {role.name}',
        request=request,
    )


def unassign_role(request, role, user):
    """Take `role` from `user` (role_change_error's rules) and log it."""
    from src.permissions import role_change_error  # v3.50.1
    error = role_change_error(request.user, role, user)
    if error:
        raise RoleChangeRefused(error, status=403)

    user.roles.remove(role)

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} removed {user.name} from role {role.name}',
        request=request,
    )


def assignable_members(role):
    """Active members not already in `role`, by name."""
    return ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        roles=role
    ).order_by('name')


def _json_body(request, message):
    try:
        return json.loads(request.body), None
    except json.JSONDecodeError:
        return None, JsonResponse({'success': False, 'error': message}, status=400)


@login_required
@officer_required
def manage_roles(request):
    """Display all roles with management options."""
    roles = list(Role.objects.all().order_by('name'))
    holders = holders_by_role(roles)

    roles_data = [
        {
            'role': role,
            'holder_count': len(holders.get(role.pk, [])),
            'holders': [name for _, name in holders.get(role.pk, [])][:5],
        }
        for role in roles
    ]

    context = {
        'roles_data': roles_data,
        'total_roles': len(roles),
        'user_is_admin': request.user.is_admin,
    }

    return render(request, 'officer/manage_roles.html', context)


@login_required
@officer_required
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
    if not request.user.is_officer:
        return JsonResponse({'success': False, 'error': ROLE_EDIT_REFUSED}, status=403)
    data, bad = _json_body(request, 'Invalid JSON data')
    if bad:
        return bad
    try:
        changes = update_role(request, role, data)
    except RoleChangeRefused as exc:
        return _refused_response(exc)

    return JsonResponse({
        'success': True,
        'message': f'Role "{role.name}" updated successfully.',
        'changes': changes,
    })


@login_required
@officer_required
@require_POST
def add_role(request):
    """Create a new role."""
    if not request.user.is_officer:  # v3.51.0
        return JsonResponse({'success': False, 'error': ROLE_EDIT_REFUSED}, status=403)
    data, bad = _json_body(request, 'Invalid JSON data')
    if bad:
        return bad
    try:
        role = create_role(request, data)
    except RoleChangeRefused as exc:
        return _refused_response(exc)

    return JsonResponse({
        'success': True,
        'message': f'Role "{role.name}" created successfully.',
        'role': {
            'id': role.id,
            'name': role.name,
            'code': role.code,
        }
    })


def _member_from_body(request):
    data, bad = _json_body(request, 'Invalid JSON')
    if bad:
        return None, bad
    user_id = data.get('user_id')
    if not user_id:
        return None, JsonResponse({'success': False, 'error': 'user_id required'}, status=400)
    return get_object_or_404(ParliamentUser, user_id=user_id), None


@login_required
@officer_required
@require_POST
def assign_role_member(request, role_id):
    """Add a member to a role."""
    role = get_object_or_404(Role, id=role_id)
    user, bad = _member_from_body(request)
    if bad:
        return bad
    try:
        assign_role(request, role, user)
    except RoleChangeRefused as exc:
        return _refused_response(exc)
    return JsonResponse({'success': True, 'message': f'{user.name} added to {role.name}.'})


@login_required
@officer_required
@require_POST
def unassign_role_member(request, role_id):
    """Remove a member from a role."""
    role = get_object_or_404(Role, id=role_id)
    user, bad = _member_from_body(request)
    if bad:
        return bad
    try:
        unassign_role(request, role, user)
    except RoleChangeRefused as exc:
        return _refused_response(exc)
    return JsonResponse({'success': True, 'message': f'{user.name} removed from {role.name}.'})


@login_required
@officer_required
def get_assignable_members(request, role_id):
    """Return active members not already in this role."""
    role = get_object_or_404(Role, id=role_id)
    members = assignable_members(role).values('user_id', 'name')
    return JsonResponse({'success': True, 'members': list(members)})


@login_required
@officer_required
@require_POST
def delete_role(request, role_id):
    """Delete a role."""
    if not request.user.is_officer:  # v3.51.0
        return JsonResponse({'success': False, 'error': ROLE_EDIT_REFUSED}, status=403)
    role = get_object_or_404(Role, id=role_id)
    try:
        role_name = delete_role_checked(request, role)
    except RoleChangeRefused as exc:
        return _refused_response(exc)

    return JsonResponse({
        'success': True,
        'message': f'Role "{role_name}" deleted successfully.',
    })
