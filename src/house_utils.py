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
