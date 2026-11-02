"""
Slating Attendance Views

Manage member attendance for a slating voting session.
Members must be marked present to cast a vote.
"""

from src.app_review import exclude_review_accounts
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from src.models import SlatingPeriod, SlatingAttendance, ParliamentUser
from .permissions import slating_chair_required

ATTENDANCE_STATUSES = ('deliberation', 'voting_open')
ATTENDANCE_CLOSED = 'Attendance can only be managed during deliberation or voting.'


def _eligible_members():
    # The App Review demo account (src/app_review.py) is not a voter.
    return exclude_review_accounts(ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ))


def attendance_roster(period):
    """
    Everyone who can be marked present (active and inactive members, chairs
    and officers), with whether they are, and the quorum. Shared with the app
    (``src/mobile/views_slating_committee.py``).
    """
    present_ids = set(
        SlatingAttendance.objects.filter(period=period).values_list('member_id', flat=True)
    )

    all_members = _eligible_members().order_by('name')

    members_with_status = [
        {'member': m, 'is_present': m.user_id in present_ids}
        for m in all_members
    ]

    present_count = len(present_ids)
    return {
        'members_with_status': members_with_status,
        'present_count': present_count,
        'total_eligible': len(members_with_status),
        'quorum_met': period.quorum is None or present_count >= period.quorum,
        'quorum_needed': max(0, (period.quorum or 0) - present_count),
    }


def mark_attendance(period, user, member_id, action):
    """
    ``mark_present`` / ``mark_absent`` one member (404 if they can't be
    marked). Returns the member; another action changes nothing.
    """
    member = get_object_or_404(_eligible_members(), user_id=member_id)

    if action == 'mark_present':
        SlatingAttendance.objects.get_or_create(
            period=period,
            member=member,
            defaults={'marked_by': user}
        )
    elif action == 'mark_absent':
        SlatingAttendance.objects.filter(period=period, member=member).delete()
    return member


@login_required
@slating_chair_required
def manage_attendance(request, period_id):
    """
    View and manage attendance for a slating period.
    Accessible during deliberation and voting_open.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status not in ATTENDANCE_STATUSES:
        messages.error(request, ATTENDANCE_CLOSED)
        return redirect('slating_period_setup', period_id=period_id)

    if request.method == 'POST':
        action = request.POST.get('action')
        member = mark_attendance(period, request.user, request.POST.get('member_id'), action)

        if action == 'mark_present':
            messages.success(request, f'{member.name} marked present.')
        elif action == 'mark_absent':
            messages.success(request, f'{member.name} marked absent.')

        return redirect('slating_attendance', period_id=period_id)

    # GET — build member list with present/absent status
    context = {'period': period, **attendance_roster(period)}

    return render(request, 'slating/attendance.html', context)
