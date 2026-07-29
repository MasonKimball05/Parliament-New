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
from collections import defaultdict

from django.db.models import Count, Q
from django.urls import reverse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.models import (
    Role, ParliamentUser, ActivityLog, RoleHistory,
    TransitionChecklistItem, TransitionChecklistStatus,
)
from src.decorators import officer_required
from src.utils.semester import transition_semesters
from src.models.users import member_defer

logger = logging.getLogger(__name__)


def _script_safe_json(data):
    """
    json.dumps that is safe to render inside a <script> block with |safe.

    Python's json.dumps does not escape '<', so a value containing
    '</script>' would otherwise terminate the script tag (stored XSS via
    e.g. a member name — flagged in the 07-09-26 auto-run report).
    Escaping '<' as \\u003c is valid JSON and neutralizes both '</script>'
    and '<!--' breakouts.
    """
    return json.dumps(data).replace('<', '\\u003c')


@login_required
@officer_required
def role_transitions(request):
    """
    Display all roles with their current holders and a transfer UI.

    Each role row shows active holders and a Transfer button. Submitting the
    modal POSTs to transfer_role which performs the atomic swap.
    """
    roles = list(Role.objects.all().order_by('name'))

    # One query for all holders instead of one per role (N+1). Selecting the
    # M2M field alongside the filter yields one row per (user, role)
    # membership; group them by role id in Python. Global name ordering
    # keeps each role's holder list name-sorted, matching prior behavior.
    holders_by_role = defaultdict(list)
    holder_rows = (
        ParliamentUser.objects.filter(roles__in=roles, member_status='Active')
        .order_by('name')
        .values('user_id', 'name', 'member_type', 'roles')
    )
    for row in holder_rows:
        holders_by_role[row.pop('roles')].append(row)

    # Open RoleHistory rows let each holder chip link to its transition
    # checklist. Keyed by (user_id, role_name); one extra query total.
    open_histories = {
        (h['user__user_id'], h['role_name']): h['id']
        for h in RoleHistory.objects.filter(
            role_name__in=[r.name for r in roles], end_semester='',
        ).values('id', 'user__user_id', 'role_name')
    }

    roles_data = []
    for role in roles:
        holders = holders_by_role.get(role.id, [])
        for holder in holders:
            holder['checklist_history_id'] = open_histories.get(
                (holder['user_id'], role.name)
            )
        roles_data.append({
            'role': role,
            'holders': holders,
            'holder_count': len(holders),
        })

    # Pass role data to JS as JSON to drive the modal without extra API calls
    roles_json = _script_safe_json([
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

    assignable_json = _script_safe_json(list(
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

    incoming_id = (data.get('incoming_user_id') or '').strip()
    outgoing_id = (data.get('outgoing_user_id') or '').strip()
    incoming_type = (data.get('incoming_type') or '').strip()
    demote_outgoing = bool(data.get('demote_outgoing', False))

    if not incoming_id:
        return JsonResponse({'success': False, 'error': 'incoming_user_id is required.'}, status=400)

    incoming = get_object_or_404(ParliamentUser, user_id=incoming_id, member_status='Active')

    changes = []
    end_semester, start_semester = transition_semesters()

    # Everything below mutates multiple rows — the docstring has always promised
    # an atomic swap, but the block was only added 07-08-26. A mid-way failure
    # now rolls the whole transfer back instead of leaving a half-transferred role.
    with transaction.atomic():
        # --- Outgoing holder ---
        if outgoing_id and outgoing_id != incoming_id:
            outgoing = get_object_or_404(ParliamentUser, user_id=outgoing_id)
            outgoing.roles.remove(role)
            changes.append(f'removed {role.name} from {outgoing.name}')

            # Close any open history rows for this role (exact-name match;
            # differently-spelled manual entries are left alone on purpose).
            RoleHistory.objects.filter(
                user=outgoing, role_name=role.name, end_semester='',
            ).update(end_semester=end_semester)

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
            RoleHistory.objects.filter(
                role_name=role.name, end_semester='',
            ).exclude(user=incoming).update(end_semester=end_semester)

        # --- Incoming holder ---
        incoming.roles.add(role)
        changes.append(f'assigned {role.name} to {incoming.name}')

        # Open a history row (skip if one is already open — double-submit safe).
        role_history = RoleHistory.objects.filter(
            user=incoming, role_name=role.name, end_semester='',
        ).first()
        if role_history is None:
            role_history = RoleHistory.objects.create(
                user=incoming, role_name=role.name, start_semester=start_semester,
            )
            changes.append(f'opened role history ({start_semester})')

        # Attach transition checklist items (role-specific + global).
        # ignore_conflicts keeps re-transfers from duplicating statuses.
        checklist_items = TransitionChecklistItem.objects.filter(
            is_active=True,
        ).filter(Q(role=role) | Q(role__isnull=True))
        TransitionChecklistStatus.objects.bulk_create(
            [
                TransitionChecklistStatus(item=item, role_history=role_history)
                for item in checklist_items
            ],
            ignore_conflicts=True,
        )

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
        'role_history_id': role_history.id,
        'checklist_url': reverse('transition_checklist', kwargs={'role_history_id': role_history.id}),
    })


def _can_access_checklist(user, role_history):
    """
    Officers, chairs, and admins can view any checklist; the incoming holder
    can view their own. Mirrors the officer_required predicate so anyone who
    can reach the Role Transitions page (which links here) can also open the
    checklists it links to — chairs were 403'd before (fixed 07-09-26).
    """
    return (
        user.is_officer
        or user.member_type == 'Chair'
        or role_history.user_id == user.pk
    )


@login_required
def transition_checklist(request, role_history_id):
    """
    Show the transition checklist for one RoleHistory (one member's term in a role).

    Accessible to officers/admins and to the holder themselves — the incoming
    member works this list while ramping up, possibly before member_type flips.
    """
    role_history = get_object_or_404(
        RoleHistory.objects.select_related('user').defer(*member_defer('user')), id=role_history_id,
    )
    if not _can_access_checklist(request.user, role_history):
        # Full-page view — render the styled 403 (same pattern as
        # src/middleware/security.py), not bare JSON.
        return render(
            request, '403.html',
            {'reason': 'This transition checklist belongs to another member.'},
            status=403,
        )

    statuses = list(
        TransitionChecklistStatus.objects
        .filter(role_history=role_history)
        .select_related('item', 'completed_by').defer(*member_defer('completed_by'))
        .order_by('item__order', 'item__id')
    )
    completed = sum(1 for s in statuses if s.completed_at is not None)
    total = len(statuses)

    context = {
        'role_history': role_history,
        'statuses': statuses,
        'completed_count': completed,
        'total_count': total,
        'progress_pct': round(100 * completed / total) if total else 0,
        'can_toggle': True,
    }
    return render(request, 'officer/transition_checklist.html', context)


@login_required
@require_POST
def toggle_checklist_item(request, status_id):
    """AJAX endpoint: toggle one checklist item's completion state."""
    status = get_object_or_404(
        TransitionChecklistStatus.objects.select_related('role_history', 'item'),
        id=status_id,
    )
    if not _can_access_checklist(request.user, status.role_history):
        return JsonResponse({'success': False, 'error': 'Not authorized.'}, status=403)

    if status.completed_at is None:
        status.completed_by = request.user
        status.completed_at = timezone.now()
    else:
        status.completed_by = None
        status.completed_at = None
    status.save(update_fields=['completed_by', 'completed_at'])

    agg = TransitionChecklistStatus.objects.filter(
        role_history=status.role_history,
    ).aggregate(
        total=Count('id'),
        done=Count('id', filter=Q(completed_at__isnull=False)),
    )
    return JsonResponse({
        'success': True,
        'completed': status.completed_at is not None,
        'completed_by': status.completed_by.name if status.completed_by else None,
        'completed_at': status.completed_at.isoformat() if status.completed_at else None,
        'progress': agg,
    })
