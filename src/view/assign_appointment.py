"""
View for assigning a chair role after an appointment vote passes.

Officers are redirected here after marking an appointment vote as passed,
or can reach it from the passed legislation page for auto-closed votes.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect

from src.models import Legislation, ParliamentUser, ActivityLog
from src.decorators import officer_required

logger = logging.getLogger(__name__)


@login_required
@officer_required
def assign_appointment(request, legislation_id):
    """
    GET  — shows the assignment confirmation page.
    POST — assigns the role to the chosen member and marks appointment_assigned.
    """
    legislation = get_object_or_404(
        Legislation,
        id=legislation_id,
        legislation_type='appointment',
    )

    if not legislation.appointment_role:
        messages.error(request, 'This appointment has no role associated with it.')
        return redirect('passed_legislation')

    is_plurality = legislation.vote_mode == 'plurality'

    if request.method == 'POST':
        # Resolve the member to assign
        if is_plurality:
            member_id = request.POST.get('assign_member_id', '').strip()
            if not member_id:
                messages.error(request, 'Please select the winning member to assign.')
                return _render_assign_page(request, legislation, is_plurality)
            member = get_object_or_404(ParliamentUser, user_id=member_id, member_status='Active')
        else:
            member = legislation.appointment_member
            if not member:
                messages.error(request, 'No nominated member found for this appointment.')
                return redirect('passed_legislation')

        role = legislation.appointment_role

        # Assign role
        member.roles.add(role)

        # Update member_type to Chair if currently Member
        if member.member_type == 'Member':
            member.member_type = 'Chair'
            member.save(update_fields=['member_type'])

        # Mark assignment complete
        legislation.appointment_assigned = True
        legislation.save(update_fields=['appointment_assigned'])

        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=(
                f'{request.user.get_display_name()} assigned {member.name} '
                f'as {role.name} following appointment vote "{legislation.title}"'
            ),
            request=request,
            metadata={
                'action': 'assign_appointment',
                'legislation_id': legislation.id,
                'legislation_title': legislation.title,
                'member_user_id': member.user_id,
                'member_name': member.name,
                'role_id': role.id,
                'role_name': role.name,
            }
        )

        logger.info(
            'User %s assigned %s to role %s via appointment vote %s',
            request.user.user_id, member.user_id, role.id, legislation.id
        )

        messages.success(
            request,
            f'{member.name} has been assigned as {role.name}.'
        )
        return redirect('passed_legislation')

    return _render_assign_page(request, legislation, is_plurality)


def _render_assign_page(request, legislation, is_plurality):
    active_members = (
        ParliamentUser.objects.filter(member_status='Active').order_by('name')
        if is_plurality else []
    )

    # For plurality, determine the winning option from vote results
    winning_option = None
    if is_plurality:
        results = legislation.get_plurality_results()
        if results:
            winning_option = results[0]['option']

    return render(request, 'assign_appointment.html', {
        'legislation': legislation,
        'role': legislation.appointment_role,
        'member': legislation.appointment_member,
        'is_plurality': is_plurality,
        'active_members': active_members,
        'winning_option': winning_option,
    })
