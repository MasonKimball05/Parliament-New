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

from src import chapter_votes
from src.models import Legislation, ParliamentUser
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

    # v3.51.0 — only a vote that closed and passed, and only once (Mason,
    # 10-19-26). Before, the page assigned the role for an open, failed or
    # already-assigned vote, and logged it "following appointment vote".
    refusal = _not_assignable(legislation)
    if refusal:
        messages.error(request, refusal)
        return redirect('passed_legislation')

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

        # v4.4.2: the admin-role rule, the locked once-only claim, the role,
        # the member type and the activity log are src/chapter_votes.py.
        try:
            message = chapter_votes.assign_appointment(legislation, request.user, member, request)
        except chapter_votes.ChapterVoteError as exc:
            messages.error(request, exc.message)
            return redirect('passed_legislation')
        messages.success(request, message)
        return redirect('passed_legislation')

    return _render_assign_page(request, legislation, is_plurality)


_not_assignable = chapter_votes.not_assignable


def _render_assign_page(request, legislation, is_plurality):
    active_members = (
        ParliamentUser.objects.filter(member_status='Active').order_by('name')
        if is_plurality else []
    )

    # For plurality, determine the winning option from vote results
    winning_option = chapter_votes.winning_option(legislation) if is_plurality else None

    return render(request, 'assign_appointment.html', {
        'legislation': legislation,
        'role': legislation.appointment_role,
        'member': legislation.appointment_member,
        'is_plurality': is_plurality,
        'active_members': active_members,
        'winning_option': winning_option,
    })
