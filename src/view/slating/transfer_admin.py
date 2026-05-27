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


@login_required
def transfer_admin(request, period_id):
    """
    Transfer slating committee admin to another user.
    Only the current admin or site admin can perform this action.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slating_committee = period.slating_committee

    if not slating_committee:
        messages.error(request, 'Slating Committee not configured.')
        return redirect('slating_dashboard')

    # Only current admin or site admin can transfer
    if slating_committee.admin != request.user and not request.user.is_admin:
        return HttpResponseForbidden('Only the committee admin can transfer control.')

    if request.method == 'POST':
        new_admin_id = request.POST.get('new_admin')
        reason = request.POST.get('reason', '').strip()

        if not new_admin_id:
            messages.error(request, 'Please select a new admin.')
            return redirect('slating_transfer_admin', period_id=period.id)

        if not reason:
            messages.error(request, 'Please provide a reason for the transfer.')
            return redirect('slating_transfer_admin', period_id=period.id)

        new_admin = get_object_or_404(ParliamentUser, id=new_admin_id)

        if new_admin == slating_committee.admin:
            messages.warning(request, 'The new admin cannot be the same as the current admin.')
            return redirect('slating_transfer_admin', period_id=period.id)

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
            user=request.user,
            action='admin_transferred',
            details=f'Admin transferred from {old_admin.name if old_admin else "None"} to {new_admin.name}. Reason: {reason}',
            metadata={
                'old_admin_id': old_admin.id if old_admin else None,
                'old_admin_name': old_admin.name if old_admin else None,
                'new_admin_id': new_admin.id,
                'new_admin_name': new_admin.name,
                'reason': reason,
            }
        )

        messages.success(request, f'Admin successfully transferred to {new_admin.name}.')
        return redirect('slating_period_setup', period_id=period.id)

    # GET: show form
    eligible_admins = ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        pk=slating_committee.admin.pk if slating_committee.admin else None
    ).order_by('name')

    context = {
        'period': period,
        'eligible_admins': eligible_admins,
        'current_admin': slating_committee.admin,
        'slating_committee': slating_committee,
    }

    return render(request, 'slating/transfer_admin.html', context)
