"""
Roles in the app (v4.x): the website's Manage Roles and Role Transitions pages
(src/view/officer/manage_roles.py, src/view/officer/transitions.py) as data.
Officers, chairs and admins open both (``officer_required``); each change then
applies the page's own narrower rule through the web views' own helpers, which
raise ``RoleChangeRefused`` (a chair can look but not change anything; only an
admin moves a role that grants admin, or touches an admin's roles).

* ``GET officer/roles/`` — ``manage_roles``: every role with its active holders,
  and what the caller may do (``can_edit``: create, change and delete role
  definitions, officers only; ``can_grant_admin``: set ``grants_admin``, admins
  only; ``can_change_holders``).
* ``POST officer/roles/`` — ``add_role`` (``create_role``).
* ``GET/PATCH/DELETE officer/roles/<id>/`` — ``role_detail`` /
  ``delete_role`` (``update_role``, ``delete_role_checked``: a role someone
  holds can't be deleted).
* ``GET officer/roles/<id>/members/`` — ``get_assignable_members``: active
  members who don't hold it. ``POST`` ``{user_id}`` — ``assign_role_member``;
  ``DELETE officer/roles/<id>/members/<user_id>/`` — ``unassign_role_member``.
* ``GET officer/transitions/`` — ``role_transitions``: every role with its
  holders (each with their open transition checklist's web path), the members
  a role can go to, and the semesters a transfer today would close and open.
* ``POST officer/transitions/<role_id>/transfer/`` — ``transfer_role``
  (``execute_role_transfer``) ``{incoming_user_id, outgoing_user_id,
  incoming_type, demote_outgoing, dry_run}``. ``dry_run: true`` runs the whole
  transfer and rolls it back, so the app's confirm step lists exactly what the
  real one will change; nothing is logged or sent.

The transition checklist itself (``transition_checklist``) stays on the
website; the app opens it there.
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer import _OfficerView
from src.models import ParliamentUser, Role
from src.permissions import role_change_error
from src.utils.semester import transition_semesters
from src.view.officer.manage_roles import (
    RoleChangeRefused, assign_role, assignable_members, create_role, delete_role_checked, holders_by_role,
    unassign_role, update_role,
)
from src.view.officer.transitions import execute_role_transfer, role_transition_rows, transition_candidates

INCOMING_TYPES = ('Officer', 'Chair', 'Member')
ROLE_FIELDS = ('name', 'code', 'description', 'one_per_chapter', 'grants_admin')


def _refused(exc):
    """RoleChangeRefused → the app's error shape."""
    if exc.status == 403:
        return exceptions.PermissionDenied(exc.message, code='forbidden')
    return BadRequest(exc.message, code='invalid_input')


def _body(request):
    body = request.data
    if not isinstance(body, dict):
        raise BadRequest('Send a JSON object.', code='invalid_input')
    return body


def _str(body, key):
    value = body.get(key)
    if value is None:
        return ''
    if not isinstance(value, str):
        raise BadRequest(f'{key} must be text.', code='invalid_input')
    return value.strip()


def _role_json(role, holders):
    return {
        'id': role.id,
        'name': role.name,
        'code': role.code,
        'description': role.description,
        'one_per_chapter': role.one_per_chapter,
        'grants_admin': role.grants_admin,
        'holder_count': len(holders),
        'holders': [{'user_id': uid, 'name': name} for uid, name in holders],
    }


def _one_role(role):
    data = _role_json(role, holders_by_role([role]).get(role.pk, []))
    # Every holder, inactive members included: what delete_role_checked
    # refuses on. `holder_count` is Active members only, as the page lists.
    data['all_holder_count'] = ParliamentUser.objects.filter(roles=role).count()
    return data


def _caller(user):
    return {
        'can_edit': bool(user.is_officer),
        'can_grant_admin': bool(user.is_admin),
        'can_change_holders': not role_change_error(user, None),
    }


# ── Manage roles ─────────────────────────────────────────────────────────────

class OfficerRoleListView(_OfficerView):
    web_page = 'manage_roles'

    def get(self, request):
        roles = list(Role.objects.all().order_by('name'))
        holders = holders_by_role(roles)
        return Response({
            'roles': [_role_json(r, holders.get(r.pk, [])) for r in roles],
            **_caller(request.user),
            'web_path': reverse('manage_roles'),
        })

    def post(self, request):
        body = _body(request)
        try:
            role = create_role(request, {k: body[k] for k in ROLE_FIELDS if k in body})
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({'role': _one_role(role)}, status=201)


class OfficerRoleView(_OfficerView):
    web_page = 'role_detail'

    def get(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        return Response({'role': _one_role(role), **_caller(request.user)})

    def patch(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        body = _body(request)
        try:
            changes = update_role(request, role, {k: body[k] for k in ROLE_FIELDS if k in body})
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({'role': _one_role(role), 'changes': changes})

    def delete(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        try:
            name = delete_role_checked(request, role)
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({'ok': True, 'name': name})


class OfficerRoleMembersView(_OfficerView):
    web_page = 'assign_role_member'

    def get(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        return Response({'members': list(assignable_members(role).values('user_id', 'name', 'member_type'))})

    def post(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        user_id = _str(_body(request), 'user_id')
        if not user_id:
            raise BadRequest('Choose a member.', code='invalid_input')
        member = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            assign_role(request, role, member)
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({'role': _one_role(role)})


class OfficerRoleMemberView(_OfficerView):
    web_page = 'unassign_role_member'

    def delete(self, request, role_id, user_id):
        role = get_object_or_404(Role, pk=role_id)
        member = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            unassign_role(request, role, member)
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({'role': _one_role(role)})


# ── Role transitions ─────────────────────────────────────────────────────────

class OfficerTransitionsView(_OfficerView):
    web_page = 'role_transitions'

    def get(self, request):
        user = request.user
        rows = role_transition_rows()
        end_semester, start_semester = transition_semesters()
        roles = []
        for d in rows:
            role = d['role']
            roles.append({
                'id': role.id,
                'name': role.name,
                'code': role.code,
                'grants_admin': role.grants_admin,
                'one_per_chapter': role.one_per_chapter,
                # The caller may move this role at all (an admin's account is
                # still refused per member, when the transfer is previewed).
                'can_transfer': not role_change_error(user, role),
                'holders': [{
                    'user_id': h['user_id'],
                    'name': h['name'],
                    'member_type': h['member_type'],
                    'checklist_web_path': (
                        reverse('transition_checklist', args=[h['checklist_history_id']])
                        if h['checklist_history_id'] else None),
                } for h in d['holders']],
            })
        filled = sum(1 for d in rows if d['holder_count'])
        return Response({
            'roles': roles,
            'members': list(transition_candidates()),
            'counts': {'total': len(rows), 'filled': filled, 'vacant': len(rows) - filled},
            'semesters': {'ending': end_semester, 'starting': start_semester},
            'can_transfer': not role_change_error(user, None),
            'incoming_types': list(INCOMING_TYPES),
            'web_path': reverse('role_transitions'),
        })


class OfficerTransferRoleView(_OfficerView):
    web_page = 'transfer_role'

    def post(self, request, role_id):
        role = get_object_or_404(Role, pk=role_id)
        body = _body(request)
        incoming_id = _str(body, 'incoming_user_id')
        outgoing_id = _str(body, 'outgoing_user_id')
        incoming_type = _str(body, 'incoming_type')
        if not incoming_id:
            raise BadRequest('Choose the incoming member.', code='invalid_input')
        if incoming_type and incoming_type not in INCOMING_TYPES:
            raise BadRequest('Member type must be Officer, Chair or Member.', code='invalid_input')
        if outgoing_id and not ParliamentUser.objects.filter(user_id=outgoing_id, roles=role).exists():
            raise BadRequest(f'That member doesn’t hold {role.name}.', code='invalid_input')
        incoming = get_object_or_404(ParliamentUser, user_id=incoming_id, member_status='Active')
        dry_run = bool(body.get('dry_run'))
        try:
            changes, role_history = execute_role_transfer(
                request, role, incoming, outgoing_id, incoming_type, bool(body.get('demote_outgoing')),
                dry_run=dry_run,
            )
        except RoleChangeRefused as exc:
            raise _refused(exc)
        return Response({
            'dry_run': dry_run,
            'message': (f'Transfer “{role.name}”?' if dry_run
                        else f'"{role.name}" transferred successfully.'),
            'changes': changes,
            'role_history_id': role_history.id if role_history else None,
            'checklist_web_path': (reverse('transition_checklist', args=[role_history.id])
                                   if role_history else None),
        })
