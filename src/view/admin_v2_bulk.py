"""
Admin v2: bulk member changes (v4.1.0).

End-of-year transitions (seniors to Alumni, officers handing roles over) used
to be one Edit Profile page per member. Here: filter, tick, choose an action,
check the preview, apply.

Two steps on purpose. "preview" works out every row's change (or why it is
skipped) and shows it; "apply" recomputes from the database (never trusting
the preview's numbers) and saves each member individually, so the existing
signals still run: role changes sync member_type, the Executive Board and Kai
permissions (src/signals.py), and status changes reach the app through
MobileDevice.still_allowed.

Refused, with the reason shown on the preview:
* your own account (a bulk change must not lock the acting admin out);
* Pledge <-> any other type: initiation has its own page (initiate_pledges),
  which does the pledge-class and tasks bookkeeping a type flip would skip;
* a one-per-chapter role for more than one member, or while someone outside
  the selection holds it.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse

from src.models import ActivityLog, ParliamentUser
from src.models.users import Role
from src.view.admin_v2 import require_admin_v2_auth

ACTIONS = {
    'set_status': 'Set status',
    'set_type': 'Set member type',
    'add_role': 'Add role',
    'remove_role': 'Remove role',
    'clear_roles': 'Remove all roles',
}
MAX_SELECTED = 300


def _filtered(params):
    qs = ParliamentUser.objects.exclude(member_status='Removed') if params.get('status') != 'Removed' \
        else ParliamentUser.objects.all()
    if params.get('status'):
        qs = qs.filter(member_status=params['status'])
    if params.get('type'):
        qs = qs.filter(member_type=params['type'])
    if params.get('pledge_class'):
        qs = qs.filter(pledge_class=params['pledge_class'])
    if params.get('role'):
        # v4.2.0: a non-numeric ?role= was a 500 (ValueError in the pk lookup).
        role = str(params['role'])
        qs = qs.filter(roles__pk=int(role)) if role.isdigit() else qs.none()
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(username__icontains=q) | Q(preferred_name__icontains=q)
                       | Q(user_id=q))
    return qs.distinct().order_by('name')


def plan(actor, members, action, value):
    """
    [{'member', 'change': text or None, 'skip': reason or None}] for `action`
    with `value`, without saving anything.
    """
    rows = []
    role = None
    if action in ('add_role', 'remove_role'):
        role = Role.objects.filter(pk=value).first() if str(value).isdigit() else None
    if action == 'add_role' and role and role.one_per_chapter:
        holders = set(ParliamentUser.objects.filter(roles=role).values_list('pk', flat=True))
        targets = {m.pk for m in members}
        if len(members) > 1:
            return [{'member': m, 'change': None,
                     'skip': f'{role.name} is one per chapter; pick one member'} for m in members]
        if holders - targets:
            return [{'member': m, 'change': None,
                     'skip': f'{role.name} is already held by someone else'} for m in members]

    for m in members:
        skip = None
        change = None
        if m.pk == actor.pk and action in ('set_status', 'set_type', 'clear_roles', 'remove_role'):
            skip = 'your own account'
        elif action == 'set_status':
            if value not in dict(ParliamentUser.MEMBER_STATUS):
                skip = 'unknown status'
            elif m.member_status == value:
                skip = f'already {value}'
            else:
                change = f'{m.member_status} → {value}'
        elif action == 'set_type':
            if value not in dict(ParliamentUser.MEMBER_TYPES):
                skip = 'unknown member type'
            elif m.member_type == value:
                skip = f'already {value}'
            elif 'Pledge' in (m.member_type, value):
                skip = 'pledges change type through Initiate members'
            else:
                change = f'{m.member_type} → {value}'
        elif action in ('add_role', 'remove_role'):
            has = role is not None and m.roles.filter(pk=role.pk).exists()
            if role is None:
                skip = 'unknown role'
            elif action == 'add_role' and has:
                skip = f'already {role.name}'
            elif action == 'remove_role' and not has:
                skip = f'not {role.name}'
            else:
                change = f'{"+" if action == "add_role" else "−"} {role.name}'
        elif action == 'clear_roles':
            names = list(m.roles.order_by('name').values_list('name', flat=True))
            if not names:
                skip = 'no roles'
            else:
                change = '− ' + ', '.join(names)
        else:
            skip = 'unknown action'
        rows.append({'member': m, 'change': change, 'skip': skip})
    return rows


def _apply(request, rows, action, value):
    role = Role.objects.filter(pk=value).first() if action in ('add_role', 'remove_role') else None
    done = 0
    with transaction.atomic():
        for row in rows:
            if row['skip']:
                continue
            m = row['member']
            if action == 'set_status':
                m.member_status = value
                m.save(update_fields=['member_status'])
            elif action == 'set_type':
                m.member_type = value
                m.save(update_fields=['member_type'])
            elif action == 'add_role':
                m.roles.add(role)
            elif action == 'remove_role':
                m.roles.remove(role)
            elif action == 'clear_roles':
                # remove(), not clear(): clear() sends pk_set=None, so the Kai and
                # Education permission resets in src/signals.py never ran (10-28-26).
                m.roles.remove(*m.roles.all())
            ActivityLog.log_activity(
                action_type='user_role_changed' if 'role' in action else 'user_edited',
                user=request.user,
                description=f'{request.user.get_display_name()} (bulk) {m.get_display_name()}: {row["change"]}',
                object_type='ParliamentUser', object_id=str(m.user_id), object_repr=m.get_display_name(),
                request=request)
            done += 1
    return done


@require_admin_v2_auth
def admin_v2_bulk_members(request):
    params = request.POST if request.method == 'POST' else request.GET
    filters = {k: params.get(k, '') for k in ('status', 'type', 'pledge_class', 'role', 'q')}
    roles = list(Role.objects.order_by('name'))

    if request.method == 'POST':
        ids = request.POST.getlist('ids')[:MAX_SELECTED]
        action = request.POST.get('bulk_action', '')
        # The list page has one dropdown per kind of value; the confirm page
        # sends `value` itself.
        kind = {'set_status': 'status', 'set_type': 'type', 'add_role': 'role', 'remove_role': 'role'}.get(action)
        value = request.POST.get('value') or (request.POST.get(f'value_{kind}', '') if kind else '')
        members = list(ParliamentUser.objects.filter(user_id__in=ids).order_by('name'))
        if not members or action not in ACTIONS:
            messages.error(request, 'Pick at least one member and an action.')
            # v4.2.0: carry only the list filters back, never the whole POST
            # (it held csrfmiddlewaretoken, which then sat in a GET URL: nginx
            # logs, browser history).
            keep = {k: v for k, v in filters.items() if v}
            return redirect(reverse('admin_v2_bulk_members') + ('?' + urlencode(keep) if keep else ''))
        rows = plan(request.user, members, action, value)
        if request.POST.get('step') == 'apply':
            done = _apply(request, rows, action, value)
            skipped = sum(1 for r in rows if r['skip'])
            messages.success(request, f'Changed {done} member(s).' + (f' {skipped} skipped.' if skipped else ''))
            return redirect('admin_v2_bulk_members')
        return render(request, 'admin_v2/bulk_members_confirm.html', {
            'rows': rows, 'action': action, 'action_label': ACTIONS[action], 'value': value,
            'value_label': next((r.name for r in roles if str(r.pk) == value), value),
            'will_change': sum(1 for r in rows if not r['skip']), 'filters': filters,
        })

    members = list(_filtered(filters)[:MAX_SELECTED])
    pledge_classes = (ParliamentUser.objects.exclude(pledge_class='')
                      .values_list('pledge_class', flat=True).distinct().order_by('-pledge_class'))
    return render(request, 'admin_v2/bulk_members.html', {
        'members': members, 'filters': filters, 'roles': roles, 'actions': ACTIONS,
        'statuses': [s for s, _ in ParliamentUser.MEMBER_STATUS],
        'types': [t for t, _ in ParliamentUser.MEMBER_TYPES],
        'pledge_classes': list(pledge_classes), 'limit': MAX_SELECTED,
    })
