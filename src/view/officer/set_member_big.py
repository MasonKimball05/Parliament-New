"""
Officer / historian-chair endpoint to set a member's big brother (v3.41.0).

Used by the house map's "unassigned" panel. Adding a little to someone is the
same write from the little's side (little.big_brother = them), so this one
endpoint covers bigs and littles both.

Same permission as set_member_house. A pledge whose big is on the education
dashboard (a PledgeBigAssignment row, draft or revealed) is refused: a draft
is secret until reveal, and a revealed pairing is education's to change
(src/big_reveal.py keeps the row and the profile in step).
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from src.house_utils import inherit_house_from_big
from src.models import ActivityLog, ParliamentUser, PledgeBigAssignment
from src.view.officer.set_member_house import _can_set_house


@login_required
@require_POST
def set_member_big(request, user_id):
    if not _can_set_house(request.user):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    target = get_object_or_404(ParliamentUser, user_id=user_id)
    if PledgeBigAssignment.objects.filter(pledge=target).exists():
        return JsonResponse(
            {'error': f"{target.get_display_name()}'s big is managed on the "
                      f'education dashboard.'}, status=409)

    big = None
    big_id = request.POST.get('big', '').strip()
    if big_id:
        big = ParliamentUser.objects.filter(user_id=big_id).exclude(
            member_status='Removed').first()
        if big is None:
            return JsonResponse({'error': 'Big not found.'}, status=400)
        # No one can be their own big, or a big of their own ancestor.
        seen, current = set(), big
        while current is not None and current.pk not in seen:
            if current.pk == target.pk:
                return JsonResponse(
                    {'error': 'That would make a loop in the family tree.'},
                    status=400)
            seen.add(current.pk)
            current = current.big_brother

    old = target.big_brother
    target.big_brother = big
    target.save(update_fields=['big_brother'])
    inherit_house_from_big(target, big)

    ActivityLog.log_activity(
        action_type='profile_updated',
        user=request.user,
        description=(
            f'{request.user.get_display_name()} set {target.get_display_name()}\'s big '
            f'to "{big.get_display_name() if big else "(none)"}" '
            f'(was "{old.get_display_name() if old else "(none)"}")'
        ),
        request=request,
        object_type='ParliamentUser',
        object_id=target.pk,
        object_repr=target.name,
        metadata={'field': 'big_brother',
                  'old': old.pk if old else None, 'new': big.pk if big else None},
    )

    return JsonResponse({'big': big.pk if big else '', 'house': target.house})
