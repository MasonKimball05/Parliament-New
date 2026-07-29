"""
Committee attendance tracking for committee chairs
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q

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

    if not (is_chair or is_officer):
        messages.error(request, 'Only committee chairs and officers can mark attendance.')
        return redirect('committee_home', code=code)

    # Get all committee members
    members = committee.members.filter(member_status='Active').order_by('name')

    # Get today's attendance records for this committee (using local timezone)
    today = timezone.localtime(timezone.now()).date()
    existing_attendance = {
        att.user_id: att
        for att in Attendance.objects.filter(
            committee=committee,
            attendance_type='committee',
            date=today
        ).select_related('user').defer(*member_defer('user'))
    }

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'mark_attendance':
            # Get lists from form
            present_ids = request.POST.getlist('present')

            updated_count = 0

            for member in members:
                # Check if marked present
                if str(member.user_id) in present_ids:
                    # Create or update attendance
                    attendance, created = Attendance.objects.update_or_create(
                        committee=committee,
                        user=member,
                        date=today,
                        attendance_type='committee',
                        defaults={
                            'status': 'present',
                            'marked_by': request.user,
                            'marked_at': timezone.now()
                        }
                    )
                    updated_count += 1
                else:
                    # Remove attendance record if unchecked
                    Attendance.objects.filter(
                        committee=committee,
                        user=member,
                        date=today,
                        attendance_type='committee'
                    ).delete()

            # Log activity
            ActivityLog.log_activity(
                action_type='other',
                user=request.user,
                description=f'Marked committee attendance for {committee.name} ({updated_count} members present)',
                request=request,
                object_type='Committee',
                object_id=committee.id,
                object_repr=str(committee)
            )

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

    # Get all attendance records for this committee
    attendance_records = Attendance.objects.filter(
        committee=committee,
        attendance_type='committee'
    ).select_related('user', 'marked_by').defer(*member_defer('user', 'marked_by')).order_by('-date', 'user__name')

    # Group by date
    attendance_by_date = {}
    for record in attendance_records:
        if record.date not in attendance_by_date:
            attendance_by_date[record.date] = []
        attendance_by_date[record.date].append(record)

    context = {
        'committee': committee,
        'attendance_by_date': dict(sorted(attendance_by_date.items(), reverse=True)),
    }

    return render(request, 'committee/attendance_history.html', context)
