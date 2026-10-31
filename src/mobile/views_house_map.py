"""
The app's House Map (v4.4.0), the native version of the website's
``house_map`` page (/house-map/).

* ``GET house-map/`` — every house in HOUSE_CHOICES order with its dot color,
  active and total counts, and its big/little family trees, built by the same
  function as the web page (``src.view.house_map.house_map_data``) so the two
  can't drift. Each tree node is a member (user id, display name, type,
  status, roll number, pledge-class color and Greek name) with their littles
  in the house nested under them. Also the "N unassigned" count and whether
  this member may manage houses (``can_set_house``).

Who sees what is the page's rule: any signed-in member, behind the page's
toggle (``@require_page_enabled('house_map')``) and pledge restriction. The
page lists every member row, so the app does too.

Read only. Assigning houses, bigs and littles (the unassigned panel and the
profile modal's house picker) and the admins' Historian Chair panel stay on
the website; ``web.page`` is the page for "Open on the website".
"""
from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.view.house_map import HOUSE_COLORS, _can_set_house, house_map_data


class HouseMapView(MobileAPIView):
    web_page = 'house_map'

    def get(self, request):
        houses, _, unassigned = house_map_data()
        for house in houses:
            house['color'] = HOUSE_COLORS.get(house['code'], '')
        return Response({
            'houses': houses,
            'unassigned_count': len(unassigned),
            'can_set_house': _can_set_house(request.user),
            'web': {'page': reverse('house_map')},
        })
