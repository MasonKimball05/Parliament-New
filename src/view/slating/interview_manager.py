"""
Slating Interview Manager Views

Schedule and manage candidate interviews.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingInterview,
    SlatingPosition, SlatingActivity, ParliamentUser
)
from .permissions import can_manage_period, can_view_applications, slating_committee_required, slating_chair_required
from src.models.users import member_defer, member_prefetch

PLEDGE_INTERVIEW = ('{name} is still a pledge. Pledges can apply, but must be initiated '
                    'before they can be interviewed.')


# ── Shared with the app (src/mobile/views_slating_committee.py) ─────────────

def period_interviews(period, status_filter=''):
    """The period's interviews, latest first; ``scheduled`` / ``completed`` / ``pending`` narrow it."""
    interviews = SlatingInterview.objects.filter(
        application__period=period
    ).select_related(
        'application', 'application__applicant'
    ).defer(*member_defer('application__applicant')).prefetch_related(
        member_prefetch('interviewers'), 'recommended_positions'
    ).order_by('-scheduled_at')

    if status_filter == 'scheduled':
        interviews = interviews.filter(
            scheduled_at__isnull=False,
            completed_at__isnull=True
        )
    elif status_filter == 'completed':
        interviews = interviews.filter(completed_at__isnull=False)
    elif status_filter == 'pending':
        interviews = interviews.filter(scheduled_at__isnull=True)
    return interviews


def apps_without_interview(period):
    """Submitted / under-review applications with no interview yet."""
    return period.applications.filter(
        status__in=['submitted', 'under_review']
    ).exclude(
        interviews__isnull=False
    ).select_related('applicant').defer(*member_defer('applicant'))


def committee_interviewers(period):
    """Who can sit on an interview panel: the committee's members and chairs, no pledges (v4.0.0)."""
    interviewers_list = []
    if period.slating_committee:
        interviewers_list = list(period.slating_committee.members.all()) + \
                          list(period.slating_committee.chairs.all())
        # Remove duplicates; pledges can't serve until initiated (v4.0.0)
        interviewers_list = [m for m in set(interviewers_list) if not m.is_pledge]
    return interviewers_list


def mark_whole_committee(interviews, committee_ids):
    """
    Set ``whole_committee`` on each interview: True when its panel includes
    everyone on the committee (11-10-26: the chapter's norm is the whole
    committee meeting each candidate, so pages say "Whole committee" rather
    than listing every name). Reads the prefetched panel; no queries.
    """
    committee_ids = set(committee_ids)
    for interview in interviews:
        panel = {m.pk for m in interview.interviewers.all()}
        interview.whole_committee = bool(committee_ids) and committee_ids <= panel
    return interviews


def schedule_refusal(user, application):
    """
    Why ``user`` may not schedule ``application``'s interview, as
    ``(code, message)`` — ``forbidden`` or ``pledge_applicant`` — or None.
    """
    # Check permissions: the period's managers (slating manager, committee
    # admin/chairs; a site admin only while the period isn't locked).
    # v4.0.0: was `user.is_admin or chair`, which let any site admin into a
    # locked period and left out the slating manager.
    # 11-06-26: any committee member may schedule (and take notes), not just
    # the chairs; setup stays with the managers.
    if not can_view_applications(user, application.period):
        return 'forbidden', 'Only the slating committee can schedule interviews.'
    # v4.0.0 (Mason): pledges may apply, but must be initiated before they're
    # interviewed.
    if application.applicant.is_pledge:
        return 'pledge_applicant', PLEDGE_INTERVIEW.format(name=application.applicant.get_display_name())
    return None


def save_schedule(application, user, scheduled_datetime, location, interviewer_ids):
    """
    Schedule (or reschedule) ``application``'s interview: time, place and
    panel (no pledges). A submitted / under-review application becomes
    interview_scheduled; logged.
    """
    period = application.period
    interview, created = SlatingInterview.objects.get_or_create(
        application=application,
        defaults={
            'scheduled_at': scheduled_datetime,
            'location': location,
        }
    )

    if not created:
        interview.scheduled_at = scheduled_datetime
        interview.location = location
        interview.save(update_fields=['scheduled_at', 'location'])

    # Set interviewers. 11-10-26: a new interview with nobody ticked gets the
    # whole committee (the chapter's norm); an existing one keeps its panel.
    if interviewer_ids:
        interviewers = ParliamentUser.objects.filter(user_id__in=interviewer_ids).exclude(member_type='Pledge')
        interview.interviewers.set(interviewers)
    elif created:
        interview.interviewers.set(committee_interviewers(period))

    # Update application status
    if application.status in ['submitted', 'under_review']:
        application.status = 'interview_scheduled'
        application.save(update_fields=['status'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='interview_scheduled',
        details=f'Scheduled interview for {application.applicant.name}',
        metadata={'application_id': application.id, 'interview_id': interview.id}
    )
    return interview


def save_completion(interview, user, notes, strengths, concerns, recommendation, position_ids):
    """
    Record an interview's notes and recommendation (marking it completed the
    first time); an interview_scheduled application becomes interviewed;
    logged.
    """
    period = interview.application.period
    interview.notes = notes
    interview.strengths = strengths
    interview.concerns = concerns
    interview.recommendation = recommendation

    # Mark as completed
    if not interview.completed_at:
        interview.completed_at = timezone.now()

    interview.save(update_fields=['notes', 'strengths', 'concerns', 'recommendation', 'completed_at'])

    # Set recommended positions
    if position_ids:
        positions = SlatingPosition.objects.filter(id__in=position_ids, period=period)
        interview.recommended_positions.set(positions)
    else:
        interview.recommended_positions.clear()

    # Update application status
    if interview.application.status == 'interview_scheduled':
        interview.application.status = 'interviewed'
        interview.application.save(update_fields=['status'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='interview_completed',
        details=f'Completed interview for {interview.application.applicant.name}',
        metadata={
            'application_id': interview.application.id,
            'interview_id': interview.id,
            'recommendation': interview.recommendation
        }
    )


class DraftConflict(Exception):
    """Someone else saved this interview since the draft's copy was loaded."""


def save_draft(interview, notes, strengths, concerns, recommendation, position_ids, base_updated=None):
    """
    The record-interview page's background save (11-12-26: notes were lost
    when the connection dropped mid-interview). Writes the form's fields
    only: it doesn't mark the interview completed, change the application's
    status or log anything; Save Interview Notes still does all that.

    ``base_updated`` is the interview's ``updated_at`` as the page last saw
    it (ISO). If it has moved on since (another committee member saved),
    raises DraftConflict rather than overwrite them. Returns the new
    ``updated_at``.
    """
    from django.utils.dateparse import parse_datetime
    if interview.notes_destroyed:
        raise DraftConflict('These notes were destroyed under the bylaws.')
    if base_updated:
        seen = parse_datetime(base_updated)
        if seen is not None and interview.updated_at and abs((interview.updated_at - seen).total_seconds()) > 0.001:
            raise DraftConflict('Someone else saved notes on this interview since you opened it.')
    interview.notes = notes
    interview.strengths = strengths
    interview.concerns = concerns
    interview.recommendation = recommendation
    interview.save(update_fields=['notes', 'strengths', 'concerns', 'recommendation', 'updated_at'])
    period = interview.application.period
    interview.recommended_positions.set(SlatingPosition.objects.filter(id__in=position_ids or [], period=period))
    return interview.updated_at


def destroy_period_notes(period, user):
    """Destroy every interview's confidential notes for ``period`` (bylaws); logged. Returns the count."""
    interviews = SlatingInterview.objects.filter(
        application__period=period,
        notes_destroyed=False
    )

    count = 0
    for interview in interviews:
        interview.destroy_notes(user)
        count += 1

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='notes_destroyed',
        details=f'Destroyed confidential notes for {count} interviews',
        metadata={'count': count}
    )
    return count


@login_required
@slating_committee_required
def interview_list(request, period_id):
    """
    View all interviews for a period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Filter options
    status_filter = request.GET.get('status', '')
    interviews = period_interviews(period, status_filter)

    # Get applications without interviews
    apps_without = apps_without_interview(period)

    # Get committee members for interviewer selection
    interviewers_list = committee_interviewers(period)
    interviews = mark_whole_committee(list(interviews), [u.pk for u in interviewers_list])

    context = {
        'period': period,
        'interviews': interviews,
        # v4.0.0: the "Destroy interview notes" section (managers only)
        'can_manage': can_manage_period(request.user, period),
        'can_schedule': can_view_applications(request.user, period),   # 11-06-26: the whole committee
        'notes_remaining': SlatingInterview.objects.filter(
            application__period=period, notes_destroyed=False).count(),
        'apps_without_interview': apps_without,
        'interviewers_list': interviewers_list,
        'status_filter': status_filter,
    }

    return render(request, 'slating/interviews.html', context)


@login_required
def schedule_interview(request, app_id):
    """
    Schedule an interview for an application.
    """
    application = get_object_or_404(SlatingApplication, id=app_id)
    period = application.period

    refusal = schedule_refusal(request.user, application)
    if refusal:
        code, message = refusal
        messages.error(request, message)
        if code == 'forbidden':
            return redirect('slating_dashboard')
        return redirect('slating_interviews', period_id=period.id)

    if request.method == 'GET':
        interviewers_list = committee_interviewers(period)

        # Check if AJAX request - return JSON for modal
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'application_id': application.id,
                'applicant_name': application.applicant.name,
                'interviewers': [
                    {'id': u.user_id, 'name': u.name}
                    for u in interviewers_list
                ]
            })

        # Regular request - render template
        existing_interview = SlatingInterview.objects.filter(application=application).first()
        # 11-10-26: a new interview starts with the whole committee ticked.
        if existing_interview:
            panel_ids = set(existing_interview.interviewers.values_list('user_id', flat=True))
        else:
            panel_ids = {u.user_id for u in interviewers_list}

        context = {
            'period': period,
            'application': application,
            'interviewers': sorted(interviewers_list, key=lambda u: u.name),
            'panel_ids': panel_ids,
            'existing_interview': existing_interview,
        }
        return render(request, 'slating/schedule_interview.html', context)

    # POST - create/update interview
    scheduled_at = request.POST.get('scheduled_at')
    location = request.POST.get('location', '').strip()
    interviewer_ids = request.POST.getlist('interviewers')

    # Parse datetime
    scheduled_datetime = None
    if scheduled_at:
        try:
            from django.utils.dateparse import parse_datetime
            scheduled_datetime = parse_datetime(scheduled_at)
        except (ValueError, TypeError):
            messages.error(request, 'Invalid date/time format.')
            return redirect('slating_interviews', period_id=period.id)

    save_schedule(application, request.user, scheduled_datetime, location, interviewer_ids)

    messages.success(request, f'Interview scheduled for {application.applicant.name}.')
    return redirect('slating_interviews', period_id=period.id)


@login_required
def complete_interview(request, interview_id):
    """
    Record interview completion and notes.
    """
    interview = get_object_or_404(SlatingInterview, id=interview_id)
    period = interview.application.period

    # Check permissions: anyone who may read interview notes for this period.
    # v4.0.0: was `user.is_admin or committee member/chair` — any site admin
    # could read a LOCKED period's confidential interview notes (the GET
    # returns them), which is exactly what the lock exists to prevent.
    user = request.user
    if not can_view_applications(user, period):
        messages.error(request, 'Only slating committee members can complete interviews.')
        return redirect('slating_dashboard')

    if request.method == 'GET':
        positions = period.positions.filter(is_active=True)

        # Check if AJAX request - return JSON for modal
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'interview_id': interview.id,
                'applicant_name': interview.application.applicant.name,
                'notes': interview.notes,
                'strengths': interview.strengths,
                'concerns': interview.concerns,
                'recommendation': interview.recommendation,
                'recommended_positions': list(interview.recommended_positions.values_list('id', flat=True)),
                'positions': [
                    {'id': p.id, 'title': p.title}
                    for p in positions
                ],
                'recommendation_choices': [
                    {'value': c[0], 'label': c[1]}
                    for c in SlatingInterview.RECOMMENDATION_CHOICES
                ]
            })

        # Regular request - render template
        context = {
            'period': period,
            'interview': interview,
            'application': interview.application,
            'positions': positions,
            # 11-09-26: one query for the ticked boxes. The template asked
            # `position in interview.recommended_positions.all` per position,
            # re-running the M2M query once per checkbox (an N+1).
            'recommended_ids': set(interview.recommended_positions.values_list('id', flat=True)),
            'recommendation_choices': SlatingInterview.RECOMMENDATION_CHOICES,
            # 11-12-26: the background draft save refuses if someone else saved since.
            'interview_updated': interview.updated_at.isoformat() if interview.updated_at else '',
        }
        return render(request, 'slating/complete_interview.html', context)

    # POST - update interview
    save_completion(interview, request.user,
                    request.POST.get('notes', '').strip(),
                    request.POST.get('strengths', '').strip(),
                    request.POST.get('concerns', '').strip(),
                    request.POST.get('recommendation', ''),
                    request.POST.getlist('recommended_positions'))

    messages.success(request, f'Interview notes saved for {interview.application.applicant.name}.')
    return redirect('slating_interviews', period_id=period.id)


@login_required
def interview_draft(request, interview_id):
    """
    POST the record-interview form's fields as a draft (background save,
    11-12-26); JSON ``{saved_at, updated}``, or 409 ``{error, conflict}``.
    Same access as the form itself.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only.'}, status=405)
    interview = get_object_or_404(SlatingInterview.objects.select_related('application__period'), id=interview_id)
    if not can_view_applications(request.user, interview.application.period):
        return JsonResponse({'error': 'Only slating committee members can take interview notes.'}, status=403)
    position_ids = [p for p in request.POST.getlist('recommended_positions') if p.isdigit()]
    try:
        updated = save_draft(interview,
                             request.POST.get('notes', '').strip(),
                             request.POST.get('strengths', '').strip(),
                             request.POST.get('concerns', '').strip(),
                             request.POST.get('recommendation', ''),
                             position_ids,
                             base_updated=request.POST.get('base_updated') or None)
    except DraftConflict as e:
        return JsonResponse({'error': str(e), 'conflict': True}, status=409)
    return JsonResponse({'saved_at': timezone.localtime(updated).strftime('%-I:%M %p'),
                         'updated': updated.isoformat()})


@login_required
@slating_chair_required
def destroy_interview_notes(request, period_id):
    """
    Destroy all confidential interview notes for a period.
    Required by bylaws after minutes approval.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Confirm action
    if request.POST.get('confirm') != 'DESTROY':
        messages.error(request, 'Please type DESTROY to confirm.')
        return redirect('slating_interviews', period_id=period_id)

    count = destroy_period_notes(period, request.user)

    messages.success(request, f'Destroyed confidential notes for {count} interviews.')
    return redirect('slating_interviews', period_id=period_id)
