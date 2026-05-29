"""
Officer views for role transition / handoff tools.

Provides an atomic swap that removes a role from an outgoing holder and assigns it
to an incoming holder in a single logged operation. Optionally updates member_type
for both parties and auto-grants admin when the role carries grants_admin.

This is distinct from manage_roles.py (which handles role definitions and individual
assign/unassign operations) — this view is focused on the semester-handoff workflow.
"""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from src.models import Role, ParliamentUser, ActivityLog
from src.decorators import officer_required

logger = logging.getLogger(__name__)


@login_required
@officer_required
def role_transitions(request):
    """
    Display all roles with their current holders and a transfer UI.

    Each role row shows active holders and a Transfer button. Submitting the
    modal POSTs to transfer_role which performs the atomic swap.
    """
    roles = Role.objects.all().order_by('name')

    roles_data = []
    for role in roles:
        holders = list(
            ParliamentUser.objects.filter(roles=role, member_status='Active')
            .order_by('name')
            .values('user_id', 'name', 'member_type')
        )
        roles_data.append({
            'role': role,
            'holders': holders,
            'holder_count': len(holders),
        })

    # Pass role data to JS as JSON to drive the modal without extra API calls
    roles_json = json.dumps([
        {
            'id': d['role'].id,
            'name': d['role'].name,
            'code': d['role'].code,
            'grants_admin': d['role'].grants_admin,
            'one_per_chapter': d['role'].one_per_chapter,
            'holders': d['holders'],
        }
        for d in roles_data
    ])

    assignable_json = json.dumps(list(
        ParliamentUser.objects.filter(member_status='Active')
        .order_by('name')
        .values('user_id', 'name', 'member_type')
    ))

    filled_count = sum(1 for d in roles_data if d['holder_count'] > 0)
    vacant_count = len(roles_data) - filled_count

    context = {
        'roles_data': roles_data,
        'roles_json': roles_json,
        'assignable_json': assignable_json,
        'total_roles': len(roles_data),
        'filled_count': filled_count,
        'vacant_count': vacant_count,
    }
    return render(request, 'officer/role_transitions.html', context)


@login_required
@officer_required
@require_POST
def transfer_role(request, role_id):
    """
    Atomically transfer a role from an outgoing holder to an incoming holder.

    Expected JSON body:
      incoming_user_id  (str, required)  — user_id of the incoming member
      outgoing_user_id  (str, optional)  — user_id of the specific outgoing holder;
                                           if omitted and role is one_per_chapter,
                                           all current holders are cleared automatically
      incoming_type     (str, optional)  — 'Officer', 'Chair', or 'Member'; updates
                                           incoming member's member_type when provided
      demote_outgoing   (bool, optional) — if true, reverts outgoing member to 'Member'
                                           when they hold no remaining roles
    """
    role = get_object_or_404(Role, id=role_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON.'}, status=400)

    incoming_id = data.get('incoming_user_id', '').strip()
    outgoing_id = data.get('outgoing_user_id', '').strip()
    incoming_type = data.get('incoming_type', '').strip()
    demote_outgoing = bool(data.get('demote_outgoing', False))

    if not incoming_id:
        return JsonResponse({'success': False, 'error': 'incoming_user_id is required.'}, status=400)

    incoming = get_object_or_404(ParliamentUser, user_id=incoming_id, member_status='Active')

    changes = []

    # --- Outgoing holder ---
    if outgoing_id and outgoing_id != incoming_id:
        outgoing = get_object_or_404(ParliamentUser, user_id=outgoing_id)
        outgoing.roles.remove(role)
        changes.append(f'removed {role.name} from {outgoing.name}')

        if demote_outgoing and outgoing.member_type in ('Officer', 'Chair'):
            # Only demote if no remaining roles justify the current type
            remaining = outgoing.roles.all()
            should_demote = (
                outgoing.member_type == 'Officer' and not remaining.filter(grants_admin=True).exists()
            ) or (
                outgoing.member_type == 'Chair' and not remaining.exists()
            )
            if should_demote:
                outgoing.member_type = 'Member'
                outgoing.save(update_fields=['member_type'])
                changes.append(f'demoted {outgoing.name} to Member')

    elif not outgoing_id and role.one_per_chapter:
        # Auto-clear all current holders for exclusive roles when none specified
        for holder in ParliamentUser.objects.filter(roles=role).exclude(user_id=incoming_id):
            holder.roles.remove(role)
            changes.append(f'removed {role.name} from {holder.name}')

    # --- Incoming holder ---
    incoming.roles.add(role)
    changes.append(f'assigned {role.name} to {incoming.name}')

    save_fields = []

    if incoming_type in ('Officer', 'Chair', 'Member') and incoming.member_type != incoming_type:
        incoming.member_type = incoming_type
        save_fields.append('member_type')
        changes.append(f'updated {incoming.name} member type to {incoming_type}')

    if role.grants_admin and not incoming.is_admin:
        incoming.is_admin = True
        save_fields.append('is_admin')
        changes.append(f'granted admin to {incoming.name}')

    if save_fields:
        incoming.save(update_fields=save_fields)

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'{request.user.get_display_name()} transferred role {role.name}: {", ".join(changes)}',
        request=request,
        metadata={
            'action': 'transfer_role',
            'role_id': role.id,
            'role_name': role.name,
            'incoming_user_id': incoming_id,
            'outgoing_user_id': outgoing_id or None,
            'changes': changes,
        }
    )

    logger.info(
        'User %s transferred role %s: %s',
        request.user.user_id, role.id, '; '.join(changes)
    )

    return JsonResponse({
        'success': True,
        'message': f'"{role.name}" transferred successfully.',
        'changes': changes,
    })
