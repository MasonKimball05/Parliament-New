"""
Committee attendance tracking for committee chairs
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q

from src.committee_management import (
    attendance_history, attendance_members, can_mark_committee_attendance, mark_committee_attendance, todays_attendance,
)
from src.models import Committee, Attendance, ParliamentUser, ActivityLog, CommitteePermissions
from src.decorators import officer_required
from src.models.users import member_defer


@login_required
def committee_attendance(request, code):
    """
    Mark committee attendance for committee members
    Only committee chairs or officers can access this
    """
    committee = get_object_or_404(Committee, code=code)

    # Check if user is a chair or officer
    is_chair = committee.is_chair(request.user)
    is_officer = request.user.member_type == 'Officer'

    if not can_mark_committee_attendance(request.user, committee):
        messages.error(request, 'Only committee chairs and officers can mark attendance.')
        return redirect('committee_home', code=code)

    # Get all committee members
    members = attendance_members(committee)

    # Get today's attendance records for this committee (using local timezone)
    today = timezone.localdate()   # v3.17.4: same value, one spelling site-wide
    existing_attendance = todays_attendance(committee)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'mark_attendance':
            # Present / not present for today, plus the activity log
            # (src/committee_management, shared with the app).
            updated_count = mark_committee_attendance(
                committee, request.user, request.POST.getlist('present'), request)

            messages.success(request, f'Attendance updated: {updated_count} members marked present.')
            return redirect('committee_attendance', code=code)

    # Build member data with current status
    member_data = []
    for member in members:
        attendance = existing_attendance.get(member.user_id)
        is_present = attendance and attendance.status == 'present'

        member_data.append({
            'user': member,
            'is_present': is_present,
            'attendance_record': attendance
        })

    # Get attendance statistics for today
    present_count = len([m for m in member_data if m['is_present']])
    total_members = len(member_data)

    context = {
        'committee': committee,
        'member_data': member_data,
        'is_chair': is_chair,
        'is_officer': is_officer,
        'today': today,
        'present_count': present_count,
        'total_members': total_members,
    }

    return render(request, 'committee/attendance.html', context)


@login_required
@officer_required
def committee_attendance_history(request, code):
    """
    View attendance history for a committee (officers only)
    """
    committee = get_object_or_404(Committee, code=code)

    context = {
        'committee': committee,
        # src/committee_management.attendance_history, shared with the app.
        # 11-06-26: each day with its present count; "Total Present" was the
        # number of records, whatever their status.
        'attendance_days': [
            {'date': day, 'records': records, 'present_n': sum(1 for r in records if r.status == 'present')}
            for day, records in attendance_history(committee).items()
        ],
    }

    return render(request, 'committee/attendance_history.html', context)
