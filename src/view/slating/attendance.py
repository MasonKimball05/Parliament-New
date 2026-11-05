"""
Slating Attendance Views

Manage member attendance for a slating voting session.
Members must be marked present to cast a vote.
"""

from urllib.parse import quote

from src.app_review import exclude_review_accounts
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from src.models import SlatingPeriod, SlatingAttendance, ParliamentUser
from .permissions import slating_chair_required

ATTENDANCE_STATUSES = ('deliberation', 'voting_open')
ATTENDANCE_CLOSED = 'Attendance can only be managed during deliberation or voting.'


def slating_chair_name(period):
    """
    Who marks people present, by name: the period's slating manager, else its
    committee's chair(s), else the committee's admin; '' if nobody is set.
    """
    if period.slating_manager_id:
        return period.slating_manager.name
    committee = period.slating_committee if period.slating_committee_id else None
    if committee is None:
        return ''
    chairs = [c.name for c in committee.chairs.all().order_by('name')]
    if chairs:
        return ' or '.join(chairs)
    return committee.admin.name if committee.admin_id else ''


def not_present_message(period):
    """
    What a member who isn't checked in sees when they try to vote (web and app;
    11-05-26). Was "You are not marked present for this session. Contact the
    slating chair."
    """
    name = slating_chair_name(period)
    who = f'{name} (slating chair)' if name else 'the slating chair'
    return f"You're not checked in yet. Ask {who} to mark you present, then tap Cast Vote again."


def _eligible_members():
    """
    The people who can be marked present: the members ``can_vote`` lets vote
    (members, chairs and officers; never pledges, advisors) who are active or
    inactive (not alumni or removed). The App Review demo account
    (src/app_review.py) is not a voter.
    """
    return exclude_review_accounts(ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ))


def attendance_counts(period):
    """Present / eligible / quorum, for the page's sticky count and the toggle's reply."""
    eligible = _eligible_members()
    present_count = SlatingAttendance.objects.filter(period=period, member__in=eligible).count()
    return {
        'present_count': present_count,
        'total_eligible': eligible.count(),
        'quorum_met': period.quorum is None or present_count >= period.quorum,
        'quorum_needed': max(0, (period.quorum or 0) - present_count),
    }


def attendance_roster(period, search=''):
    """
    Everyone who can be marked present (active and inactive members, chairs
    and officers), with whether they are, and the quorum. Shared with the app
    (``src/mobile/views_slating_committee.py``). ``search`` narrows the list
    (name or roll number); the counts are always the whole roster's.
    """
    present_ids = set(
        SlatingAttendance.objects.filter(period=period).values_list('member_id', flat=True)
    )

    all_members = _eligible_members().order_by('name')
    search = (search or '').strip()
    if search:
        all_members = all_members.filter(Q(name__icontains=search) | Q(preferred_name__icontains=search)
                                         | Q(role_number__icontains=search))

    members_with_status = [
        {'member': m, 'is_present': m.user_id in present_ids}
        for m in all_members
    ]

    return {
        'members_with_status': members_with_status,
        **attendance_counts(period),
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


def mark_all_present(period, user, member_ids=None):
    """
    Mark present every eligible member in ``member_ids`` (the list the chair
    is looking at, e.g. after a search), or everyone eligible when it's None.
    Ids that aren't eligible are ignored. Returns how many were newly marked.
    """
    members = _eligible_members()
    if member_ids is not None:
        members = members.filter(user_id__in=[str(i) for i in member_ids if str(i).strip()])
    already = set(SlatingAttendance.objects.filter(period=period).values_list('member_id', flat=True))
    new = [SlatingAttendance(period=period, member=m, marked_by=user)
           for m in members.only('pk') if m.pk not in already]
    SlatingAttendance.objects.bulk_create(new, ignore_conflicts=True)
    return len(new)


def _wants_json(request):
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('Accept', ''))


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

    search = (request.GET.get('q') or request.POST.get('q') or '').strip()
    back = redirect(f"{request.path}?q={quote(search)}" if search else request.path)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'mark_all_present':
            # The page sends the ids it's listing (``listed``, after a search, maybe
            # none); a bare mark_all_present means everyone eligible.
            ids = request.POST.getlist('member_ids') if request.POST.get('listed') else None
            added = mark_all_present(period, request.user, ids)
            if _wants_json(request):
                return JsonResponse({'ok': True, 'added': added,
                                     'present_ids': [str(i) for i in SlatingAttendance.objects.filter(
                                         period=period).values_list('member_id', flat=True)],
                                     **attendance_counts(period)})
            messages.success(request, f'{added} member{"" if added == 1 else "s"} marked present.')
            return back

        member = mark_attendance(period, request.user, request.POST.get('member_id'), action)
        if _wants_json(request):
            return JsonResponse({'ok': True, 'member_id': member.user_id, 'name': member.name,
                                 'present': SlatingAttendance.objects.filter(period=period, member=member).exists(),
                                 **attendance_counts(period)})

        if action == 'mark_present':
            messages.success(request, f'{member.name} marked present.')
        elif action == 'mark_absent':
            messages.success(request, f'{member.name} marked absent.')

        return back

    # GET — build member list with present/absent status
    context = {'period': period, 'search': search, **attendance_roster(period, search)}

    return render(request, 'slating/attendance.html', context)
