"""
Adding, editing and initiating members in the app (v4.x, officer tools batch
5), through the Member List's own helpers (src/view/officer/manage_members.py)
so the checks, the activity log, the welcome email and the initiation
notification are the website's:

* ``GET officer/members/form/`` — what the add and edit forms offer: every role
  (``get_all_roles``), the member types and statuses, and which of them this
  officer may give (a chair makes only Members and Pledges; only an admin
  gives a role that grants admin).
* ``POST officer/members/add/`` ``{name, user_id?, email?, member_type,
  member_status, roles?}`` — ``add_member``. The reply carries the new
  account's username and temporary password, once, as the web modal shows them.
* ``GET officer/members/<user_id>/edit/`` — ``edit_member``'s current values;
  ``POST`` ``{name?, preferred_name?, email?, member_type?, member_status?,
  role_number?, roles?}`` changes only the fields sent.
* ``POST officer/members/initiate/`` ``{pledges: [{user_id, role_number}]}`` —
  ``initiate_pledges``: the pledges become Members with those roll numbers.

All ``officer_required`` (officers, chairs, admins). Bulk import and "Sync
officer admins" stay on the website. Passwords are never set or shown here
beyond the new account's temporary one.
"""
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer_tools import _Officers
from src.models import ParliamentUser, Role
from src.permissions import member_type_change_error, role_change_error
from src.view.officer.manage_members import (
    MemberChangeRefused, apply_member_edit, create_member, initiate_pledge_batch, member_edit_data,
)


def _refused(exc):
    """The website's refusal as the app's error: 403 stays forbidden, a 500 stays a 500."""
    if exc.status == 403:
        return exceptions.PermissionDenied(exc.message, code='forbidden')
    err = BadRequest(exc.message, code='invalid_input')
    err.status_code = exc.status
    return err


def _body(request):
    if not isinstance(request.data, dict):
        raise BadRequest('Invalid JSON data', code='invalid_input')
    return request.data


class MemberFormView(_Officers):
    web_page = 'get_all_roles'

    def get(self, request):
        actor = request.user
        types = [t for t, _ in ParliamentUser.MEMBER_TYPES]
        return Response({
            'roles': [{
                'id': r.id,
                'name': r.name,
                'code': r.code,
                'grants_admin': r.grants_admin,
                # Whether this officer may give or take it (role_change_error;
                # an admin's account is checked again on save).
                'can_assign': not role_change_error(actor, r),
            } for r in Role.objects.order_by('name')],
            'types': types,
            # The types a new account may have (member_type_change_error).
            'addable_types': [t for t in types if not member_type_change_error(actor, t)],
            'statuses': [s for s, _ in ParliamentUser.MEMBER_STATUS],
            'is_admin': bool(actor.is_admin),
            'can_change_roles': not role_change_error(actor, None),
        })


class MemberAddView(_Officers):
    web_page = 'add_member'
    parser_classes = [JSONParser]

    def post(self, request):
        try:
            result = create_member(request.user, _body(request), request)
        except MemberChangeRefused as exc:
            raise _refused(exc)
        return Response(result, status=201)


class MemberEditView(_Officers):
    web_page = 'edit_member'
    parser_classes = [JSONParser]

    def _member(self, user_id):
        return get_object_or_404(ParliamentUser, user_id=user_id)

    def get(self, request, user_id):
        member = self._member(user_id)
        actor = request.user
        types = [t for t, _ in ParliamentUser.MEMBER_TYPES]
        return Response({
            'success': True,
            'member': member_edit_data(member),
            # What this officer may change on this account (the save checks again).
            'can_edit': not (member.is_admin and not actor.is_admin and member.pk != actor.pk),
            'allowed_types': [t for t in types if not member_type_change_error(actor, t, member)],
            'assignable_role_ids': [r.id for r in Role.objects.all() if not role_change_error(actor, r, member)],
        })

    def post(self, request, user_id):
        member = self._member(user_id)
        try:
            result = apply_member_edit(request.user, member, _body(request), request)
        except MemberChangeRefused as exc:
            raise _refused(exc)
        return Response(result)


class InitiatePledgesView(_Officers):
    web_page = 'initiate_pledges'
    parser_classes = [JSONParser]

    def post(self, request):
        try:
            result = initiate_pledge_batch(request.user, _body(request), request)
        except MemberChangeRefused as exc:
            raise _refused(exc)
        return Response(result)
