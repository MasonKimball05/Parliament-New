"""
Officer / historian-chair endpoint to assign a member's house.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from src.models import ParliamentUser, ActivityLog
from src.house_utils import propagate_house

VALID_HOUSES = {c[0] for c in ParliamentUser.HOUSE_CHOICES}


def _can_set_house(user):
    """Officers always can. Chairs can if they hold any role with 'historian' in the name."""
    if user.member_type == 'Officer' or user.is_admin:
        return True
    if user.member_type == 'Chair':
        return user.roles.filter(name__icontains='historian').exists()
    return False


@login_required
@require_POST
def set_member_house(request, user_id):
    if not _can_set_house(request.user):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    target = get_object_or_404(ParliamentUser, user_id=user_id)
    house = request.POST.get('house', '').strip()

    if house and house not in VALID_HOUSES:
        return JsonResponse({'error': 'Invalid house.'}, status=400)

    old_house = target.house
    target.house = house
    target.save(update_fields=['house'])

    # Cascade to houseless descendants
    if house:
        for little in target.little_brothers.filter(house=''):
            propagate_house(little, house)

    ActivityLog.log_activity(
        action_type='profile_updated',
        user=request.user,
        description=(
            f'{request.user.get_display_name()} set {target.get_display_name()}\'s house '
            f'to "{house or "(none)"}" (was "{old_house or "(none)"}")'
        ),
        request=request,
        object_type='ParliamentUser',
        object_id=target.pk,
        object_repr=target.name,
        metadata={'field': 'house', 'old': old_house, 'new': house},
    )

    return JsonResponse({'house': house})
