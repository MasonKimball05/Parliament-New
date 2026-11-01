"""
Officer / historian-chair endpoint to assign a member's house.

The rule and the write live in src/house_utils.py (``can_set_house`` /
``assign_house``), shared with the app's ``officer/members/<id>/house/``.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from src.house_utils import FamilyChangeRefused, assign_house, can_set_house
from src.models import ParliamentUser


@login_required
@require_POST
def set_member_house(request, user_id):
    if not can_set_house(request.user):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    target = get_object_or_404(ParliamentUser, user_id=user_id)
    try:
        house = assign_house(request, target, request.POST.get('house', ''))
    except FamilyChangeRefused as e:
        return JsonResponse({'error': e.message}, status=e.status)
    return JsonResponse({'house': house})
