"""
Assigning houses and bigs in the app (v4.x): the website's
``set_member_house`` / ``set_member_big`` endpoints (the directory and house
map profile modal's house picker, and the house map's "N unassigned" panel),
for the same people: officers, admins, and chairs holding a historian role
(``house_utils.can_set_house``). The member card (``members/<id>/``) says
``can_set_house`` so the app knows to offer them.

* ``GET officer/house-assignments/`` — the unassigned panel's data
  (``unassigned_panel_data``, behind the house map's page toggle): the houses,
  who is unassigned, and everyone pickable as a big or little with their
  house, big and whether education manages their big (``locked``).
* ``POST officer/members/<user_id>/house/`` ``{house}`` — '' clears it. The
  house cascades to houseless littles. → ``{house}``.
* ``POST officer/members/<user_id>/big/`` ``{big}`` — a member's user id, ''
  clears it. Adding a little is this on the little. 409 ``big_managed`` when
  the pairing is on the education dashboard; 400 for an unknown big or a loop.
  → ``{big, house}`` (the house they may have inherited).

Both writes go through ``house_utils.assign_house`` / ``assign_big``, the
website endpoints' own code, so the activity log rows are the same.
"""
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.house_utils import FamilyChangeRefused, assign_big, assign_house, can_set_house
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import ParliamentUser


class Conflict(exceptions.APIException):
    status_code = 409


class _HouseManagerView(MobileAPIView):
    web_page_toggle = False   # set_member_house / set_member_big have no page toggle

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not can_set_house(request.user):
            raise exceptions.PermissionDenied('Only officers and the Historian can set houses.', code='forbidden')


def _refused(e):
    if e.status == 409:
        return Conflict(e.message, code='big_managed')
    return BadRequest(e.message, code='invalid_input')


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


class HouseAssignmentsView(_HouseManagerView):
    web_page = 'house_map'
    web_page_toggle = True    # the panel lives on the house map, which has one

    def get(self, request):
        from src.view.house_map import house_map_data, unassigned_panel_data
        _, all_members, unassigned = house_map_data()
        panel = unassigned_panel_data(all_members, unassigned)
        return Response({
            'houses': [{'value': v, 'label': label} for v, label in panel['houses']],
            'unassigned': panel['unassigned'],
            'people': [{
                'user_id': p['id'],
                'name': p['name'],
                'member_type': p['type'],
                'member_status': p['status'],
                'role_number': p['roll'],
                'house': p['house'],
                'big': p['big'],
                'locked': p['locked'],
            } for p in panel['people']],
        })


class MemberHouseView(_HouseManagerView):
    web_page = 'set_member_house'

    def post(self, request, user_id):
        target = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            house = assign_house(request, target, str(_body(request).get('house') or ''))
        except FamilyChangeRefused as e:
            raise _refused(e)
        return Response({'house': house})


class MemberBigView(_HouseManagerView):
    web_page = 'set_member_big'

    def post(self, request, user_id):
        target = get_object_or_404(ParliamentUser, user_id=user_id)
        try:
            big = assign_big(request, target, str(_body(request).get('big') or ''))
        except FamilyChangeRefused as e:
            raise _refused(e)
        return Response({'big': big.pk if big else '', 'house': target.house})
