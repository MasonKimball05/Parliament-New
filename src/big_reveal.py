"""
Pledge big reveals (v3.39.0, 10-01-26).

`PledgeBigAssignment` is the education committee's draft pairing. Nothing
outside the education dashboard sees it until it is revealed, and revealing
means copying it into `ParliamentUser.big_brother`, which the rest of the site
already reads (profile card, house map, profile page).

Every write to that field on behalf of a reveal goes through this module so
that the draft row and the profile can't disagree.

Timed reveals are applied by the `tasks.reveal_due_bigs` Celery beat job
(every minute) and, as a backstop if beat is down, at the top of the pledge's
My Tasks page and the education dashboard. `reveal()` is idempotent and
claims the row with a conditional UPDATE, so the two can race safely.
"""
from django.db import transaction
from django.utils import timezone

from src.house_utils import inherit_house_from_big


def eligible_bigs():
    """Who can be a big: active, non-pledge members."""
    from src.models import ParliamentUser
    return (
        ParliamentUser.objects
        .filter(is_active=True, member_status='Active')
        .exclude(member_type='Pledge')
        .order_by('name')
    )


def _apply_to_profile(assignment):
    pledge = assignment.pledge
    pledge.big_brother = assignment.big
    pledge.save(update_fields=['big_brother'])
    inherit_house_from_big(pledge, assignment.big)


def reveal(assignment, now=None):
    """
    Make `assignment` live. Returns True if this call revealed it, False if it
    was already revealed (by someone else, or by the beat job).
    """
    from src.models import PledgeBigAssignment
    now = now or timezone.now()
    with transaction.atomic():
        claimed = (
            PledgeBigAssignment.objects
            .filter(pk=assignment.pk, revealed_at__isnull=True)
            .update(revealed_at=now)
        )
        if not claimed:
            return False
        assignment.revealed_at = now
        _apply_to_profile(assignment)
    return True


def unreveal(assignment):
    """
    Undo a reveal: back to a draft, and clear the pledge's profile big if it
    is still this assignment's big. A house the pledge inherited at reveal
    is left alone; houses are set by hand elsewhere and guessing which one to
    restore would be worse.
    """
    from src.models import PledgeBigAssignment
    with transaction.atomic():
        PledgeBigAssignment.objects.filter(pk=assignment.pk).update(revealed_at=None)
        assignment.revealed_at = None
        pledge = assignment.pledge
        if pledge.big_brother_id == assignment.big_id:
            pledge.big_brother = None
            pledge.save(update_fields=['big_brother'])


def change_big(assignment, new_big):
    """Change who the big is. On a revealed pairing the profile follows."""
    old_big_id = assignment.big_id
    assignment.big = new_big
    with transaction.atomic():
        assignment.save(update_fields=['big', 'updated_at'])
        if assignment.is_revealed:
            pledge = assignment.pledge
            if pledge.big_brother_id in (old_big_id, None):
                _apply_to_profile(assignment)


def reveal_due_bigs(now=None):
    """Reveal every timed pairing whose time has come. Returns how many."""
    from src.models import PledgeBigAssignment
    now = now or timezone.now()
    due = (
        PledgeBigAssignment.objects
        .filter(reveal_mode='timed', revealed_at__isnull=True,
                reveals_at__isnull=False, reveals_at__lte=now)
        .select_related('pledge', 'big')
    )
    return sum(1 for a in due if reveal(a, now=now))


def profile_big_is_locked(user):
    """True once education has revealed this user's big (profile_view lock)."""
    from src.models import PledgeBigAssignment
    return PledgeBigAssignment.objects.filter(
        pledge=user, revealed_at__isnull=False,
    ).exists()
