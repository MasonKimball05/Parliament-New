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
from django.db import connection

from src.models import ParliamentUser, Role, ActivityLog
from src.forms import AddMemberForm, EditMemberForm
from src.decorators import officer_required
from src.notification_service import notify_users
from src.notifications import send_pledge_welcome_email

logger = logging.getLogger(__name__)


def generate_temp_password(length=12):
    """Generate a random alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_username(name):
    """
    Generate username from name: first letter of first name + last name (lowercase).
    E.g., "John Smith" -> "jsmith", "Mary Jane Watson" -> "mwatson"
    """
    parts = name.strip().split()
    if len(parts) < 2:
        # Single name, just use it lowercase
        return parts[0].lower() if parts else 'user'

    first_initial = parts[0][0].lower()
    last_name = parts[-1].lower()

    # Remove any non-alphanumeric characters
    import re
    username = re.sub(r'[^a-z0-9]', '', first_initial + last_name)

    return username if username else 'user'


def ensure_unique_username(base_username):
    """
    Ensure the username is unique by appending numbers if needed.
    E.g., if "jsmith" exists, try "jsmith1", "jsmith2", etc.
    """
    username = base_username
    counter = 1

    while ParliamentUser.objects.filter(username=username).exists():
        username = f"{base_username}{counter}"
        counter += 1

    return username


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

    # Generate username from name (first initial + last name)
    base_username = generate_username(form.cleaned_data['name'])
    username = ensure_unique_username(base_username)

    # For pledges: password = username
    # For others: random password
    member_type = form.cleaned_data['member_type']
    if member_type == 'Pledge':
        temp_password = username
    else:
        temp_password = generate_temp_password()

    # Create the user
    user = ParliamentUser.objects.create_user(
        user_id=form.cleaned_data['user_id'],
        name=form.cleaned_data['name'],
        username=username,
        member_type=member_type,
        password=temp_password,
    )

    # Set additional fields
    user.member_status = form.cleaned_data['member_status']
    if form.cleaned_data.get('email'):
        user.email = form.cleaned_data['email']
    user.force_password_change = True
    user.has_default_password = True
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

    # For pledges, send welcome email if an email address was provided
    is_pledge = user.member_type == 'Pledge'
    if is_pledge and user.email:
        try:
            send_pledge_welcome_email(user, temp_password)
        except Exception as e:
            logger.error(f"Failed to send pledge welcome email for {user.user_id}: {e}")

    return JsonResponse({
        'success': True,
        'member': {
            'user_id': user.user_id,
            'name': user.name,
            'username': user.username,
            'email': user.email or '',
            'member_type': user.member_type,
            'member_status': user.member_status,
        },
        'username': user.username,
        'temp_password': temp_password,
        'password_is_username': is_pledge,
        'message': f'Member {user.name} created successfully.',
    })


@login_required
@officer_required
@require_http_methods(['GET', 'POST'])
def edit_member(request, user_id):
    """Edit an existing member's details."""
    member = get_object_or_404(ParliamentUser, user_id=user_id)

    if request.method == 'GET':
        # Calculate last login display
        from django.utils import timezone
        from django.utils.timezone import localtime
        if member.last_login:
            days_ago = (timezone.now() - member.last_login).days
            local_last_login = localtime(member.last_login)
            if days_ago == 0:
                last_login_display = local_last_login.strftime('%b %d, %Y at %I:%M %p') + ' (Today)'
            elif days_ago == 1:
                last_login_display = local_last_login.strftime('%b %d, %Y at %I:%M %p') + ' (Yesterday)'
            else:
                last_login_display = local_last_login.strftime('%b %d, %Y at %I:%M %p') + f' ({days_ago} days ago)'
        else:
            last_login_display = 'Never logged in'

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
                'last_login': last_login_display,
                'has_default_password': member.has_default_password,
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

    # Check for existing user_ids that would conflict with the new role numbers
    # (since we're changing user_id to match role_number)
    existing_user_ids = ParliamentUser.objects.filter(
        user_id__in=role_number_values
    ).exclude(
        user_id__in=pledge_ids  # Exclude the pledges being initiated
    ).values_list('user_id', flat=True)

    if existing_user_ids:
        return JsonResponse({
            'success': False,
            'error': f'Member ID(s) already in use: {", ".join(existing_user_ids)}'
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

    # Get the actual database table name
    table_name = ParliamentUser._meta.db_table

    # Non-Django-ORM tables: FK relations not discoverable via _meta.get_fields().
    # Django model relations are handled automatically below via ORM.
    extra_tables = [
        ('calendar_subscriptions', 'user_id'),
    ]

    for pledge in pledges:
        old_user_id = pledge.user_id
        assigned_role_number = role_numbers.get(old_user_id)
        pledge_name = pledge.name

        try:
            from django.db import transaction
            with transaction.atomic():
                # Steps 1–3: Raw SQL to copy the user record with the new PK.
                # Django ORM cannot update primary keys, so this must stay as raw SQL.
                with connection.cursor() as cursor:
                    # Step 1: Temporarily rename unique fields on old record to avoid conflicts
                    cursor.execute(
                        f"""UPDATE {table_name}
                            SET username = '_migrating_' || username,
                                email = '_migrating_' || email
                            WHERE user_id = %s""",  # nosec B608
                        [old_user_id]
                    )

                    # Step 2: Create a copy of the pledge with the new user_id
                    cursor.execute(
                        """SELECT column_name FROM information_schema.columns
                           WHERE table_name = %s AND column_name NOT IN ('user_id', 'username', 'email')
                           ORDER BY ordinal_position""",
                        [table_name]
                    )
                    columns = [row[0] for row in cursor.fetchall()]
                    columns_str = ', '.join(columns)

                    cursor.execute(
                        f"""INSERT INTO {table_name} (user_id, username, email, {columns_str})
                            SELECT %s,
                                   REPLACE(username, '_migrating_', ''),
                                   REPLACE(email, '_migrating_', ''),
                                   {columns_str}
                            FROM {table_name} WHERE user_id = %s""",  # nosec B608
                        [assigned_role_number, old_user_id]
                    )

                    # Step 3: Update member_type and role_number on the new record
                    cursor.execute(
                        f"UPDATE {table_name} SET member_type = %s, role_number = %s WHERE user_id = %s",  # nosec B608
                        ['Member', assigned_role_number, assigned_role_number]
                    )

                # Step 4: Reassign all FK / OneToOne / M2M relations using Django ORM.
                # _meta.get_fields() auto-discovers every relation, so new models are
                # covered automatically without any changes here.
                old_pledge_obj = ParliamentUser.objects.get(user_id=old_user_id)
                new_pledge_obj = ParliamentUser.objects.get(user_id=assigned_role_number)

                # M2M: roles (through table, must be moved before ORM scan)
                new_pledge_obj.roles.set(list(old_pledge_obj.roles.all()))
                old_pledge_obj.roles.clear()

                # M2M: co-authored legislation
                try:
                    for leg in list(old_pledge_obj.co_authored_legislation.all()):
                        leg.co_authors.remove(old_pledge_obj)
                        leg.co_authors.add(new_pledge_obj)
                except Exception:
                    pass

                # Reverse FK / OneToOne — auto-discovered
                skip_accessors = {'roles', 'co_authored_legislation'}
                fk_update_summary = []
                for rel in old_pledge_obj._meta.get_fields():
                    if not hasattr(rel, 'get_accessor_name'):
                        continue
                    accessor = rel.get_accessor_name()
                    if not accessor or accessor in skip_accessors:
                        continue
                    if rel.one_to_many or rel.one_to_one:
                        try:
                            related_manager = getattr(old_pledge_obj, accessor)
                            count = related_manager.all().update(**{rel.field.name: new_pledge_obj})
                            if count > 0:
                                fk_update_summary.append(f"{accessor}: {count}")
                        except Exception as e:
                            logger.warning(f"Could not update FK {accessor} for {pledge_name}: {e}")

                # Non-ORM tables (not discoverable via _meta)
                with connection.cursor() as cursor:
                    for rel_table, rel_column in extra_tables:
                        try:
                            cursor.execute(
                                f"UPDATE {rel_table} SET {rel_column} = %s WHERE {rel_column} = %s",  # nosec B608
                                [assigned_role_number, old_user_id]
                            )
                            if cursor.rowcount > 0:
                                fk_update_summary.append(f"{rel_table}.{rel_column}: {cursor.rowcount}")
                        except Exception as e:
                            if 'does not exist' not in str(e).lower() and 'undefined' not in str(e).lower():
                                logger.error(f"Error updating extra table {rel_table}.{rel_column}: {e}")

                if fk_update_summary:
                    logger.info(f"FK updates for {pledge_name}: {', '.join(fk_update_summary)}")

                # Step 5: Verify no CASCADE-delete FKs still reference the old user
                cascade_tables = [
                    ('src_servicehourssubmission', 'submitted_by_id'),
                    ('src_servicehoursadjustment', 'member_id'),
                    ('src_servicememberexpectation', 'member_id'),
                    ('src_attendance', 'user_id'),
                    ('src_vote', 'user_id'),
                    ('src_committeevote', 'user_id'),
                ]
                with connection.cursor() as cursor:
                    for check_table, check_col in cascade_tables:
                        try:
                            cursor.execute(
                                f"SELECT COUNT(*) FROM {check_table} WHERE {check_col} = %s",  # nosec B608
                                [old_user_id]
                            )
                            count = cursor.fetchone()[0]
                            if count > 0:
                                logger.error(f"CRITICAL: {count} rows in {check_table}.{check_col} still reference {old_user_id}")
                                raise ValueError(f"Cannot delete old user - {count} records in {check_table} still reference it")
                        except Exception as e:
                            if 'does not exist' not in str(e).lower() and 'undefined' not in str(e).lower():
                                if 'Cannot delete' in str(e):
                                    raise
                                logger.debug(f"Check skipped for {check_table}: {e}")

                    # Step 6: Delete the old pledge record (now has no FK references)
                    cursor.execute(
                        f"DELETE FROM {table_name} WHERE user_id = %s",  # nosec B608
                        [old_user_id]
                    )
                    rows_updated = cursor.rowcount

            if rows_updated == 0:
                logger.warning(f"Failed to delete old pledge record for {old_user_id}")
                continue

            # Refresh the pledge object with the new user_id
            pledge = ParliamentUser.objects.get(user_id=assigned_role_number)

            initiated_names.append(pledge_name)
            initiated_users.append(pledge)
            role_number_assignments.append(f"{pledge_name} (#{assigned_role_number})")

        except Exception as e:
            logger.error(f"Error initiating pledge {old_user_id}: {e}", exc_info=True)
            # Also log to admin_actions so it appears in the main log file
            import traceback
            logging.getLogger('admin_actions').error(
                f"INITIATE FAILED: pledge {old_user_id} ({pledge_name}): {e}\n{traceback.format_exc()}"
            )
            return JsonResponse({
                'success': False,
                'error': f'Error initiating {pledge_name}: {str(e)}'
            }, status=500)

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


@login_required
def get_admin_roles(request):
    """API endpoint to get roles that grant admin privileges."""
    admin_roles = Role.objects.filter(grants_admin=True).values('id', 'name', 'code')
    return JsonResponse({
        'success': True,
        'roles': list(admin_roles),
    })


# Protected user ID that can never have admin removed
PROTECTED_ADMIN_USER_ID = '73'


@login_required
@require_POST
def sync_officer_admins(request):
    """
    Sync admin status based on officer roles.
    Users with roles that have grants_admin=True are made admins.
    Users who no longer have these roles lose admin (except protected users).
    Only admins can perform this action.
    """
    # Check admin permission
    if not request.user.is_admin:
        return JsonResponse({
            'success': False,
            'error': 'Only admins can sync officer admin status.'
        }, status=403)

    try:
        results = {
            'added': [],
            'removed': [],
            'protected': [],
            'unchanged': [],
        }

        # Get all roles that grant admin (dynamic from database)
        admin_roles = Role.objects.filter(grants_admin=True)

        if not admin_roles.exists():
            return JsonResponse({
                'success': False,
                'error': 'No roles are configured to grant admin privileges. Please set grants_admin=True on at least one role.'
            }, status=400)

        users_with_admin_roles = ParliamentUser.objects.filter(
            roles__in=admin_roles,
            member_status='Active'
        ).distinct()

        # Get all current admins
        current_admins = ParliamentUser.objects.filter(is_admin=True)

        # Add admin to users with admin roles who don't have it
        for user in users_with_admin_roles:
            if not user.is_admin:
                user.is_admin = True
                user.save(update_fields=['is_admin'])
                results['added'].append({
                    'user_id': user.user_id,
                    'name': user.name,
                    'roles': list(user.roles.filter(grants_admin=True).values_list('name', flat=True))
                })
            else:
                results['unchanged'].append({
                    'user_id': user.user_id,
                    'name': user.name,
                })

        # Remove admin from users who no longer have admin roles
        users_with_admin_role_ids = set(users_with_admin_roles.values_list('user_id', flat=True))

        for admin_user in current_admins:
            # Skip if user has admin roles
            if admin_user.user_id in users_with_admin_role_ids:
                continue

            # Protect user ID 73
            if admin_user.user_id == PROTECTED_ADMIN_USER_ID:
                results['protected'].append({
                    'user_id': admin_user.user_id,
                    'name': admin_user.name,
                    'reason': 'Protected admin account'
                })
                continue

            # Remove admin
            admin_user.is_admin = False
            admin_user.save(update_fields=['is_admin'])
            results['removed'].append({
                'user_id': admin_user.user_id,
                'name': admin_user.name,
            })

        # Log the activity
        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.get_display_name()} synced officer admin status: {len(results["added"])} added, {len(results["removed"])} removed',
            request=request,
            metadata={
                'action': 'sync_officer_admins',
                'added': results['added'],
                'removed': results['removed'],
                'protected': results['protected'],
            }
        )

        logger.info(f"Admin {request.user.user_id} synced officer admins: {len(results['added'])} added, {len(results['removed'])} removed")

        return JsonResponse({
            'success': True,
            'message': f'Admin sync complete: {len(results["added"])} added, {len(results["removed"])} removed.',
            'results': results,
        })

    except Exception as e:
        logger.error(f"Error syncing officer admins: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': f'An error occurred: {str(e)}'
        }, status=500)


# ---------------------------------------------------------------------------
# Bulk / Batch member import
# ---------------------------------------------------------------------------

VALID_MEMBER_TYPES = {mt[0] for mt in ParliamentUser.MEMBER_TYPES}
VALID_MEMBER_STATUSES = {ms[0] for ms in ParliamentUser.MEMBER_STATUS}
BULK_IMPORT_MAX_ROWS = 100


def _import_member_row(row, requesting_user):
    """
    Attempt to create a single member from a dict of fields.
    Returns a result dict:
      { 'status': 'created'|'error', 'name': ..., 'username': ...,
        'error': ..., 'temp_password': ..., 'password_is_username': ... }
    """
    import re
    name = (row.get('name') or '').strip()
    username = (row.get('username') or '').strip().lower()
    member_type = (row.get('member_type') or '').strip()
    email = (row.get('email') or '').strip() or None
    pledge_class = (row.get('pledge_class') or '').strip() or ''
    phone_number = (row.get('phone_number') or '').strip() or ''
    graduation_year_raw = (row.get('graduation_year') or '').strip()

    # --- Required field validation ---
    if not name:
        return {'status': 'error', 'name': name or '(blank)', 'username': username, 'error': 'Name is required.'}
    if not username:
        return {'status': 'error', 'name': name, 'username': '(blank)', 'error': 'Username is required.'}
    if not re.match(r'^[a-z0-9_]+$', username):
        return {'status': 'error', 'name': name, 'username': username, 'error': 'Username may only contain lowercase letters, numbers, and underscores.'}
    if member_type not in VALID_MEMBER_TYPES:
        return {'status': 'error', 'name': name, 'username': username,
                'error': f"Invalid member_type '{member_type}'. Must be one of: {', '.join(sorted(VALID_MEMBER_TYPES))}."}

    # --- Username conflict ---
    if ParliamentUser.objects.filter(username=username).exists():
        # Suggest appending a number
        counter = 1
        while ParliamentUser.objects.filter(username=f"{username}{counter}").exists():
            counter += 1
        suggestion = f"{username}{counter}"
        return {'status': 'error', 'name': name, 'username': username,
                'error': f"Username '{username}' is already taken. Try '{suggestion}'."}

    # --- Email conflict ---
    if email and ParliamentUser.objects.filter(email__iexact=email).exists():
        return {'status': 'error', 'name': name, 'username': username,
                'error': f"Email '{email}' is already in use by another account."}

    # --- Optional field validation ---
    graduation_year = None
    if graduation_year_raw:
        try:
            graduation_year = int(graduation_year_raw)
            if not (2000 <= graduation_year <= 2100):
                raise ValueError
        except ValueError:
            return {'status': 'error', 'name': name, 'username': username,
                    'error': f"Invalid graduation_year '{graduation_year_raw}'. Must be a 4-digit year."}

    # --- Generate user_id (same logic as single add: first initial + last name) ---
    parts = name.strip().split()
    if len(parts) >= 2:
        base_id = re.sub(r'[^a-z0-9]', '', (parts[0][0] + parts[-1]).lower())
    else:
        base_id = re.sub(r'[^a-z0-9]', '', parts[0].lower()) if parts else 'user'
    user_id = base_id
    id_counter = 1
    while ParliamentUser.objects.filter(user_id=user_id).exists():
        user_id = f"{base_id}{id_counter}"
        id_counter += 1

    # --- Password ---
    if member_type == 'Pledge':
        temp_password = username
        password_is_username = True
    else:
        temp_password = generate_temp_password()
        password_is_username = False

    # --- Create ---
    user = ParliamentUser.objects.create_user(
        user_id=user_id,
        name=name,
        username=username,
        member_type=member_type,
        password=temp_password,
    )
    user.force_password_change = True
    user.has_default_password = True
    if email:
        user.email = email
    if pledge_class:
        user.pledge_class = pledge_class
    if phone_number:
        user.phone_number = phone_number
    if graduation_year:
        user.graduation_year = graduation_year
    user.save()

    ActivityLog.log_activity(
        action_type='other',
        user=requesting_user,
        description=f'{requesting_user.get_display_name()} bulk-imported member {user.name} ({user.user_id})',
        metadata={'action': 'bulk_import_member', 'new_member_id': user.user_id,
                  'new_member_name': user.name, 'member_type': user.member_type},
    )

    return {
        'status': 'created',
        'name': name,
        'username': username,
        'user_id': user_id,
        'temp_password': temp_password,
        'password_is_username': password_is_username,
    }


@login_required
@officer_required
@require_POST
def bulk_import_members(request):
    """
    Bulk-create members from either a JSON batch or a CSV file upload.

    JSON batch (from the Batch Add modal):
      Content-Type: application/json
      Body: [{"name": "...", "username": "...", "member_type": "..."}, ...]

    CSV upload (from the Import CSV modal):
      Content-Type: multipart/form-data
      File field: "csv_file"
      Required columns: name, username, member_type
      Optional columns: email, pledge_class, graduation_year, phone_number

    Response: {"results": [...], "created": N, "errors": N}
    """
    import csv
    import io

    # --- Parse input ---
    if request.content_type and 'application/json' in request.content_type:
        try:
            rows = json.loads(request.body)
            if not isinstance(rows, list):
                return JsonResponse({'success': False, 'error': 'Expected a JSON array.'}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON.'}, status=400)
    else:
        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return JsonResponse({'success': False, 'error': 'No CSV file provided.'}, status=400)
        if not csv_file.name.endswith('.csv'):
            return JsonResponse({'success': False, 'error': 'File must be a .csv file.'}, status=400)

        try:
            content = csv_file.read().decode('utf-8-sig')  # strip BOM if present
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
        except Exception as e:
            return JsonResponse({'success': False, 'error': f'Could not parse CSV: {e}'}, status=400)

        # Normalise header names (strip whitespace)
        rows = [{k.strip().lower(): v for k, v in row.items()} for row in rows]

    if not rows:
        return JsonResponse({'success': False, 'error': 'No rows found.'}, status=400)
    if len(rows) > BULK_IMPORT_MAX_ROWS:
        return JsonResponse({'success': False,
                             'error': f'Maximum {BULK_IMPORT_MAX_ROWS} rows per import. Split into smaller batches.'}, status=400)

    # --- Process rows ---
    results = []
    for row in rows:
        results.append(_import_member_row(row, request.user))

    created = sum(1 for r in results if r['status'] == 'created')
    errors = sum(1 for r in results if r['status'] == 'error')

    logger.info(f"Officer {request.user.user_id} bulk-imported members: {created} created, {errors} errors")

    return JsonResponse({'success': True, 'results': results, 'created': created, 'errors': errors})
