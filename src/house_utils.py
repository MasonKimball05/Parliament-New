"""
Shared utilities for house assignment and propagation.
"""
from src.models import ParliamentUser


def propagate_house(start_user, house):
    """
    Set house on start_user (if not already set) and cascade down the
    big/little family tree to any descendants who have no house assigned.
    Stops at anyone who already has a different house explicitly set.
    Does NOT override an existing house assignment.
    """
    queue = [start_user]
    updated = []
    while queue:
        current = queue.pop()
        if current.house:
            continue  # already has a house — respect it, don't cascade through
        current.house = house
        current.save(update_fields=['house'])
        updated.append(current)
        for little in current.little_brothers.filter(house=''):
            queue.append(little)
    return updated


def inherit_house_from_big(user, big_brother):
    """
    If the big brother has a house and the user does not, propagate the
    big's house down through the user and their houseless descendants.
    Returns the list of users updated (may be empty).
    """
    if not big_brother or not big_brother.house or user.house:
        return []
    return propagate_house(user, big_brother.house)


# ── Assigning a house or a big (set_member_house / set_member_big) ───────────
#
# The website's two endpoints and the app's (src/mobile/views_member_family.py)
# both go through these, so who may, what's refused and the activity log row
# are the same either way.

VALID_HOUSES = {c[0] for c in ParliamentUser.HOUSE_CHOICES}


class FamilyChangeRefused(Exception):
    """A house or big change the website refuses: ``status`` is its HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def can_set_house(user):
    """Officers and admins always can. Chairs can if they hold any role with 'historian' in the name."""
    if user.member_type == 'Officer' or user.is_admin:
        return True
    if user.member_type == 'Chair':
        return user.roles.filter(name__icontains='historian').exists()
    return False


def big_managed_by_education(target):
    """
    True when ``target``'s big is on the education dashboard (a
    PledgeBigAssignment row, draft or revealed): a draft is secret until the
    reveal, and a revealed pairing is education's to change.
    """
    from src.models import PledgeBigAssignment
    return PledgeBigAssignment.objects.filter(pledge=target).exists()


def assign_house(request, target, house):
    """
    Set ``target``'s house ('' clears it), cascade it to their houseless
    littles, and log it. Returns the new house; raises FamilyChangeRefused.
    """
    from src.models import ActivityLog
    house = (house or '').strip()
    if house and house not in VALID_HOUSES:
        raise FamilyChangeRefused('Invalid house.')

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
    return house


def assign_big(request, target, big_id):
    """
    Set ``target``'s big brother to the member ``big_id`` ('' clears it),
    inherit the big's house if ``target`` has none, and log it. Returns the
    big (or None). Raises FamilyChangeRefused: 409 when education manages the
    pairing, 400 for an unknown big or one that would make a loop.
    """
    from src.models import ActivityLog
    if big_managed_by_education(target):
        raise FamilyChangeRefused(
            f"{target.get_display_name()}'s big is managed on the education dashboard.", status=409)

    big = None
    big_id = (big_id or '').strip()
    if big_id:
        big = ParliamentUser.objects.filter(user_id=big_id).exclude(member_status='Removed').first()
        if big is None:
            raise FamilyChangeRefused('Big not found.')
        # No one can be their own big, or a big of their own ancestor.
        seen, current = set(), big
        while current is not None and current.pk not in seen:
            if current.pk == target.pk:
                raise FamilyChangeRefused('That would make a loop in the family tree.')
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
    return big
