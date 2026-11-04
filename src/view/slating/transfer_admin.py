"""
Slating Committee Admin Transfer View

Allows the current admin (typically President) to transfer admin control
to another user, for example if they are running for office.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.utils import timezone

from src.models import SlatingPeriod, SlatingActivity, ParliamentUser


# ── Shared with the app (src/mobile/views_slating_setup.py, 11-04-26) ────────

class TransferError(Exception):
    """A refusal: ``code`` for the app, the web's message, and its level."""

    def __init__(self, code, message, level='error'):
        super().__init__(message)
        self.code = code
        self.level = level


def transfer_refusal(user, period):
    """
    Why ``user`` may not transfer the period's committee admin, as
    ``(code, message)`` — or None. Only the current committee admin or a site
    admin may (the page has no chair or locked-period check of its own).
    """
    slating_committee = period.slating_committee
    if not slating_committee:
        return 'no_committee', 'Slating Committee not configured.'
    if slating_committee.admin != user and not user.is_admin:
        return 'forbidden', 'Only the committee admin can transfer control.'
    return None


def eligible_new_admins(slating_committee):
    """Active members other than the current admin, by name."""
    return ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        pk=slating_committee.admin.pk if slating_committee.admin else None
    ).order_by('name')


def transfer_committee_admin(period, user, new_admin_id, reason):
    """
    Make ``new_admin_id`` (a member's user id, ParliamentUser's primary key)
    the committee's admin, record the transfer on the period and log it.
    Raises TransferError for a missing member or reason, or the same admin;
    Http404 for an unknown member. Returns the new admin.

    11-04-26: this looked members up by ``id`` and logged ``.id``, which
    ParliamentUser doesn't have (its key is ``user_id``), and the page's
    picker posted ``member.id`` (blank) — so no transfer ever went through.
    """
    slating_committee = period.slating_committee
    reason = (reason or '').strip()

    if not new_admin_id:
        raise TransferError('invalid_input', 'Please select a new admin.')

    if not reason:
        raise TransferError('invalid_input', 'Please provide a reason for the transfer.')

    new_admin = get_object_or_404(ParliamentUser, pk=new_admin_id)

    if new_admin == slating_committee.admin:
        raise TransferError('same_admin', 'The new admin cannot be the same as the current admin.', level='warning')

    old_admin = slating_committee.admin

    # Update committee admin
    slating_committee.admin = new_admin
    slating_committee.save()

    # Update period tracking
    period.admin_transferred = True
    period.admin_transfer_reason = reason
    period.admin_transferred_at = timezone.now()
    period.admin_transferred_from = old_admin
    period.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='admin_transferred',
        details=f'Admin transferred from {old_admin.name if old_admin else "None"} to {new_admin.name}. Reason: {reason}',
        metadata={
            'old_admin_id': old_admin.pk if old_admin else None,
            'old_admin_name': old_admin.name if old_admin else None,
            'new_admin_id': new_admin.pk,
            'new_admin_name': new_admin.name,
            'reason': reason,
        }
    )
    return new_admin


@login_required
def transfer_admin(request, period_id):
    """
    Transfer slating committee admin to another user.
    Only the current admin or site admin can perform this action.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slating_committee = period.slating_committee

    refusal = transfer_refusal(request.user, period)
    if refusal and refusal[0] == 'no_committee':
        messages.error(request, refusal[1])
        return redirect('slating_dashboard')

    # Only current admin or site admin can transfer
    if refusal:
        return HttpResponseForbidden(refusal[1])

    if request.method == 'POST':
        try:
            new_admin = transfer_committee_admin(
                period, request.user, request.POST.get('new_admin'), request.POST.get('reason', ''))
        except TransferError as e:
            getattr(messages, e.level)(request, str(e))
            return redirect('slating_transfer_admin', period_id=period.id)

        messages.success(request, f'Admin successfully transferred to {new_admin.name}.')
        return redirect('slating_period_setup', period_id=period.id)

    # GET: show form
    eligible_admins = eligible_new_admins(slating_committee)

    context = {
        'period': period,
        'eligible_admins': eligible_admins,
        'current_admin': slating_committee.admin,
        'slating_committee': slating_committee,
    }

    return render(request, 'slating/transfer_admin.html', context)
