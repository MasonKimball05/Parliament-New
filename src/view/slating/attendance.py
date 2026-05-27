"""
Slating Attendance Views

Manage member attendance for a slating voting session.
Members must be marked present to cast a vote.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import SlatingPeriod, SlatingAttendance, ParliamentUser
from .permissions import slating_chair_required


@login_required
@slating_chair_required
def manage_attendance(request, period_id):
    """
    View and manage attendance for a slating period.
    Accessible during deliberation and voting_open.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status not in ['deliberation', 'voting_open']:
        messages.error(request, 'Attendance can only be managed during deliberation or voting.')
        return redirect('slating_period_setup', period_id=period_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        member_id = request.POST.get('member_id')

        member = get_object_or_404(
            ParliamentUser,
            user_id=member_id,
            member_status__in=['Active', 'Inactive'],
            member_type__in=['Member', 'Chair', 'Officer']
        )

        if action == 'mark_present':
            SlatingAttendance.objects.get_or_create(
                period=period,
                member=member,
                defaults={'marked_by': request.user}
            )
            messages.success(request, f'{member.name} marked present.')

        elif action == 'mark_absent':
            SlatingAttendance.objects.filter(period=period, member=member).delete()
            messages.success(request, f'{member.name} marked absent.')

        return redirect('slating_attendance', period_id=period_id)

    # GET — build member list with present/absent status
    present_ids = set(
        SlatingAttendance.objects.filter(period=period).values_list('member_id', flat=True)
    )

    all_members = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ).order_by('name')

    members_with_status = [
        {'member': m, 'is_present': m.user_id in present_ids}
        for m in all_members
    ]

    present_count = len(present_ids)
    quorum_met = period.quorum is None or present_count >= period.quorum
    quorum_needed = max(0, (period.quorum or 0) - present_count)

    context = {
        'period': period,
        'members_with_status': members_with_status,
        'present_count': present_count,
        'total_eligible': len(members_with_status),
        'quorum_met': quorum_met,
        'quorum_needed': quorum_needed,
    }

    return render(request, 'slating/attendance.html', context)
