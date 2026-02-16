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
from .permissions import slating_committee_required, slating_chair_required


@login_required
@slating_committee_required
def interview_list(request, period_id):
    """
    View all interviews for a period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Get all interviews
    interviews = SlatingInterview.objects.filter(
        application__period=period
    ).select_related(
        'application', 'application__applicant'
    ).prefetch_related(
        'interviewers', 'recommended_positions'
    ).order_by('-scheduled_at')

    # Filter options
    status_filter = request.GET.get('status', '')

    if status_filter == 'scheduled':
        interviews = interviews.filter(
            scheduled_at__isnull=False,
            completed_at__isnull=True
        )
    elif status_filter == 'completed':
        interviews = interviews.filter(completed_at__isnull=False)
    elif status_filter == 'pending':
        interviews = interviews.filter(scheduled_at__isnull=True)

    # Get applications without interviews
    apps_without_interview = period.applications.filter(
        status__in=['submitted', 'under_review']
    ).exclude(
        interviews__isnull=False
    ).select_related('applicant')

    # Get committee members for interviewer selection
    interviewers_list = []
    if period.slating_committee:
        interviewers_list = list(period.slating_committee.members.all()) + \
                          list(period.slating_committee.chairs.all())
        interviewers_list = list(set(interviewers_list))  # Remove duplicates

    context = {
        'period': period,
        'interviews': interviews,
        'apps_without_interview': apps_without_interview,
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

    # Check permissions - must be admin or committee chair
    user = request.user
    is_authorized = user.is_admin
    if not is_authorized and period.slating_committee:
        is_authorized = period.slating_committee.is_chair(user)

    if not is_authorized:
        messages.error(request, 'Only slating committee chairs can schedule interviews.')
        return redirect('slating_dashboard')

    if request.method == 'GET':
        # Get potential interviewers
        interviewers_list = []
        if period.slating_committee:
            interviewers_list = list(period.slating_committee.members.all()) + \
                              list(period.slating_committee.chairs.all())
            interviewers_list = list(set(interviewers_list))

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

        context = {
            'period': period,
            'application': application,
            'interviewers': interviewers_list,
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

    # Get or create interview
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
        interview.save()

    # Set interviewers
    if interviewer_ids:
        interviewers = ParliamentUser.objects.filter(user_id__in=interviewer_ids)
        interview.interviewers.set(interviewers)

    # Update application status
    if application.status in ['submitted', 'under_review']:
        application.status = 'interview_scheduled'
        application.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='interview_scheduled',
        details=f'Scheduled interview for {application.applicant.name}',
        metadata={'application_id': application.id, 'interview_id': interview.id},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Interview scheduled for {application.applicant.name}.')
    return redirect('slating_interviews', period_id=period.id)


@login_required
def complete_interview(request, interview_id):
    """
    Record interview completion and notes.
    """
    interview = get_object_or_404(SlatingInterview, id=interview_id)
    period = interview.application.period

    # Check permissions - must be admin or committee member
    user = request.user
    is_authorized = user.is_admin
    if not is_authorized and period.slating_committee:
        committee = period.slating_committee
        is_authorized = committee.is_member(user) or committee.is_chair(user)

    if not is_authorized:
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
            'recommendation_choices': SlatingInterview.RECOMMENDATION_CHOICES,
        }
        return render(request, 'slating/complete_interview.html', context)

    # POST - update interview
    interview.notes = request.POST.get('notes', '').strip()
    interview.strengths = request.POST.get('strengths', '').strip()
    interview.concerns = request.POST.get('concerns', '').strip()
    interview.recommendation = request.POST.get('recommendation', '')

    # Mark as completed
    if not interview.completed_at:
        interview.completed_at = timezone.now()

    interview.save()

    # Set recommended positions
    position_ids = request.POST.getlist('recommended_positions')
    if position_ids:
        positions = SlatingPosition.objects.filter(id__in=position_ids, period=period)
        interview.recommended_positions.set(positions)
    else:
        interview.recommended_positions.clear()

    # Update application status
    if interview.application.status == 'interview_scheduled':
        interview.application.status = 'interviewed'
        interview.application.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='interview_completed',
        details=f'Completed interview for {interview.application.applicant.name}',
        metadata={
            'application_id': interview.application.id,
            'interview_id': interview.id,
            'recommendation': interview.recommendation
        },
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Interview notes saved for {interview.application.applicant.name}.')
    return redirect('slating_interviews', period_id=period.id)


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
        return redirect('slating_period_setup', period_id=period_id)

    # Get all interviews with notes
    interviews = SlatingInterview.objects.filter(
        application__period=period,
        notes_destroyed=False
    )

    count = 0
    for interview in interviews:
        interview.destroy_notes(request.user)
        count += 1

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='notes_destroyed',
        details=f'Destroyed confidential notes for {count} interviews',
        metadata={'count': count},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Destroyed confidential notes for {count} interviews.')
    return redirect('slating_period_setup', period_id=period_id)
