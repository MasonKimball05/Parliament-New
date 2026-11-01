"""
Officer / historian-chair endpoint to set a member's big brother (v3.41.0).

Used by the house map's "unassigned" panel. Adding a little to someone is the
same write from the little's side (little.big_brother = them), so this one
endpoint covers bigs and littles both.

Same permission as set_member_house. A pledge whose big is on the education
dashboard (a PledgeBigAssignment row, draft or revealed) is refused: a draft
is secret until reveal, and a revealed pairing is education's to change
(src/big_reveal.py keeps the row and the profile in step).

The rule and the write live in src/house_utils.py (``assign_big``), shared
with the app's ``officer/members/<id>/big/``.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from src.house_utils import FamilyChangeRefused, assign_big, can_set_house
from src.models import ParliamentUser


@login_required
@require_POST
def set_member_big(request, user_id):
    if not can_set_house(request.user):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    target = get_object_or_404(ParliamentUser, user_id=user_id)
    try:
        big = assign_big(request, target, request.POST.get('big', ''))
    except FamilyChangeRefused as e:
        return JsonResponse({'error': e.message}, status=e.status)
    return JsonResponse({'big': big.pk if big else '', 'house': target.house})
