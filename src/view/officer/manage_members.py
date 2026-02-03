"""
Officer views for member management: add, edit, delete, and batch initiate pledges.
"""
import json
import secrets
import string
import logging

from django.shortcuts import get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods

from src.models import ParliamentUser, Role, ActivityLog
from src.forms import AddMemberForm, EditMemberForm
from src.decorators import officer_required
from src.notification_service import notify_users

logger = logging.getLogger(__name__)


def generate_temp_password(length=12):
    """Generate a random alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@login_required
@officer_required
@require_POST
def add_member(request):
    """Add a new member with auto-generated password."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    form = AddMemberForm(data)

    if not form.is_valid():
        errors = {field: error[0] for field, error in form.errors.items()}
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    # Generate a temporary password
    temp_password = generate_temp_password()

    # Create the user
    user = ParliamentUser.objects.create_user(
        user_id=form.cleaned_data['user_id'],
        name=form.cleaned_data['name'],
        username=form.cleaned_data['name'],  # Use name as username initially
        member_type=form.cleaned_data['member_type'],
        password=temp_password,
    )

    # Set additional fields
    user.member_status = form.cleaned_data['member_status']
    if form.cleaned_data.get('email'):
        user.email = form.cleaned_data['email']
    user.force_password_change = True
    user.save()

    # Set roles
    roles = form.cleaned_data.get('roles', [])
    if roles:
        user.roles.set(roles)

    # Log the activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} added new member {user.name} ({user.user_id})',
        request=request,
        metadata={
            'action': 'add_member',
            'new_member_id': user.user_id,
            'new_member_name': user.name,
            'member_type': user.member_type,
        }
    )

    logger.info(f"Officer {request.user.user_id} added new member {user.user_id}")

    return JsonResponse({
        'success': True,
        'member': {
            'user_id': user.user_id,
            'name': user.name,
            'email': user.email or '',
            'member_type': user.member_type,
            'member_status': user.member_status,
        },
        'temp_password': temp_password,
        'message': f'Member {user.name} created successfully.',
    })


@login_required
@officer_required
@require_http_methods(['GET', 'POST'])
def edit_member(request, user_id):
    """Edit an existing member's details."""
    member = get_object_or_404(ParliamentUser, user_id=user_id)

    if request.method == 'GET':
        # Return current member data for the modal
        return JsonResponse({
            'success': True,
            'member': {
                'user_id': member.user_id,
                'name': member.name,
                'preferred_name': member.preferred_name or '',
                'email': member.email or '',
                'member_type': member.member_type,
                'member_status': member.member_status,
                'roles': list(member.roles.values_list('id', flat=True)),
                'is_admin': member.is_admin,
                'role_number': member.role_number or '',
            }
        })

    # POST - Update member
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    # Track changes for logging
    changes = []

    # Update basic fields
    if 'name' in data and data['name'] != member.name:
        changes.append(f"name: {member.name} -> {data['name']}")
        member.name = data['name']

    if 'preferred_name' in data:
        new_val = data['preferred_name'] or ''
        old_val = member.preferred_name or ''
        if new_val != old_val:
            changes.append(f"preferred_name: {old_val} -> {new_val}")
            member.preferred_name = new_val if new_val else ''

    if 'email' in data:
        new_email = data['email'] or None
        if new_email != member.email:
            # Check for duplicate email
            if new_email and ParliamentUser.objects.filter(email=new_email).exclude(pk=member.pk).exists():
                return JsonResponse({'success': False, 'error': 'A member with this email already exists.'}, status=400)
            changes.append(f"email: {member.email} -> {new_email}")
            member.email = new_email

    if 'member_type' in data and data['member_type'] != member.member_type:
        changes.append(f"member_type: {member.member_type} -> {data['member_type']}")
        member.member_type = data['member_type']

    if 'member_status' in data and data['member_status'] != member.member_status:
        changes.append(f"member_status: {member.member_status} -> {data['member_status']}")
        member.member_status = data['member_status']

    # Handle role_number (only for non-pledges)
    if 'role_number' in data:
        new_role_number = data['role_number'].strip() if data['role_number'] else None
        old_role_number = member.role_number
        if new_role_number != old_role_number:
            # Check for duplicates if setting a new role number
            if new_role_number:
                existing = ParliamentUser.objects.filter(role_number=new_role_number).exclude(pk=member.pk)
                if existing.exists():
                    return JsonResponse({
                        'success': False,
                        'error': f'Role number {new_role_number} is already assigned to another member.'
                    }, status=400)
            changes.append(f"role_number: {old_role_number} -> {new_role_number}")
            member.role_number = new_role_number

    member.save()

    # Handle roles
    if 'roles' in data:
        old_role_ids = set(member.roles.values_list('id', flat=True))
        new_role_ids = set(data['roles']) if data['roles'] else set()
        if old_role_ids != new_role_ids:
            old_role_names = list(member.roles.values_list('name', flat=True))
            member.roles.set(new_role_ids)
            new_role_names = list(member.roles.values_list('name', flat=True))
            changes.append(f"roles: {old_role_names} -> {new_role_names}")

    # Log the activity if there were changes
    if changes:
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.get_display_name()} edited member {member.name}: {", ".join(changes)}',
            request=request,
            metadata={
                'action': 'edit_member',
                'member_id': member.user_id,
                'member_name': member.name,
                'changes': changes,
            }
        )
        logger.info(f"Officer {request.user.user_id} edited member {member.user_id}: {changes}")

    return JsonResponse({
        'success': True,
        'message': f'Member {member.name} updated successfully.',
        'changes': changes,
    })


@login_required
@officer_required
@require_POST
def delete_member(request, user_id):
    """Delete or deactivate a member."""
    member = get_object_or_404(ParliamentUser, user_id=user_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        data = {}

    # Prevent deleting yourself
    if member.user_id == request.user.user_id:
        return JsonResponse({'success': False, 'error': 'You cannot delete your own account.'}, status=400)

    # Prevent deleting admins
    if member.is_admin:
        return JsonResponse({'success': False, 'error': 'Cannot delete admin accounts. Please contact a system administrator.'}, status=400)

    hard_delete = data.get('hard_delete', False)

    if hard_delete:
        member_name = member.name
        member_id = member.user_id
        member.delete()
        action_desc = f'{request.user.get_display_name()} permanently deleted member {member_name} ({member_id})'
    else:
        # Soft delete - set status to Inactive
        member.member_status = 'Inactive'
        member.is_active = False
        member.save()
        action_desc = f'{request.user.get_display_name()} deactivated member {member.name} ({member.user_id})'

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=action_desc,
        request=request,
        metadata={
            'action': 'delete_member' if hard_delete else 'deactivate_member',
            'member_id': user_id,
            'hard_delete': hard_delete,
        }
    )

    logger.info(f"Officer {request.user.user_id} {'deleted' if hard_delete else 'deactivated'} member {user_id}")

    return JsonResponse({
        'success': True,
        'message': 'Member permanently deleted.' if hard_delete else 'Member deactivated.',
    })


@login_required
@officer_required
@require_POST
def initiate_pledges(request):
    """Batch initiate pledges to full members with role number assignment."""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON data'}, status=400)

    # Expect a list of {user_id, role_number} objects
    pledge_data = data.get('pledges', [])

    if not pledge_data:
        return JsonResponse({'success': False, 'error': 'No pledges selected.'}, status=400)

    # Extract user_ids and role_numbers
    pledge_ids = [p['user_id'] for p in pledge_data]
    role_numbers = {p['user_id']: p.get('role_number', '').strip() for p in pledge_data}

    # Validate all role numbers are provided
    missing_role_numbers = [uid for uid, rn in role_numbers.items() if not rn]
    if missing_role_numbers:
        return JsonResponse({
            'success': False,
            'error': 'All pledges must have a role number assigned.'
        }, status=400)

    # Check for duplicate role numbers in the submission
    role_number_values = list(role_numbers.values())
    if len(role_number_values) != len(set(role_number_values)):
        return JsonResponse({
            'success': False,
            'error': 'Duplicate role numbers detected. Each role number must be unique.'
        }, status=400)

    # Check for existing role numbers in the database
    existing_role_numbers = ParliamentUser.objects.filter(
        role_number__in=role_number_values
    ).values_list('role_number', flat=True)

    if existing_role_numbers:
        return JsonResponse({
            'success': False,
            'error': f'Role number(s) already in use: {", ".join(existing_role_numbers)}'
        }, status=400)

    # Get pledges that are actually pledges
    pledges = ParliamentUser.objects.filter(
        user_id__in=pledge_ids,
        member_type='Pledge'
    )

    if not pledges.exists():
        return JsonResponse({'success': False, 'error': 'No valid pledges found in selection.'}, status=400)

    # Initiate each pledge with their role number
    initiated_names = []
    initiated_users = []
    role_number_assignments = []

    for pledge in pledges:
        assigned_role_number = role_numbers.get(pledge.user_id)
        pledge.member_type = 'Member'
        pledge.role_number = assigned_role_number
        pledge.save()
        initiated_names.append(pledge.name)
        initiated_users.append(pledge)
        role_number_assignments.append(f"{pledge.name} (#{assigned_role_number})")

    # Send notifications to initiated members
    try:
        notify_users(
            initiated_users,
            'announcement',
            'Welcome to the Chapter!',
            message='Congratulations! You have been initiated as a full member.',
            link='/',
            source_type='Initiation',
            source_id=None,
        )
    except Exception as e:
        logger.error(f"Failed to send initiation notifications: {e}", exc_info=True)

    # Log the activity
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} initiated {len(initiated_names)} pledges: {", ".join(role_number_assignments)}',
        request=request,
        metadata={
            'action': 'initiate_pledges',
            'count': len(initiated_names),
            'pledge_names': initiated_names,
            'role_numbers': role_numbers,
            'pledge_ids': pledge_ids,
        }
    )

    logger.info(f"Officer {request.user.user_id} initiated {len(initiated_names)} pledges with role numbers")

    return JsonResponse({
        'success': True,
        'message': f'Successfully initiated {len(initiated_names)} pledge(s) as full members.',
        'count': len(initiated_names),
        'names': initiated_names,
        'role_numbers': role_number_assignments,
    })


@login_required
@officer_required
def get_all_roles(request):
    """API endpoint to get all available roles."""
    roles = Role.objects.all().values('id', 'name', 'code')
    return JsonResponse({
        'success': True,
        'roles': list(roles),
    })
