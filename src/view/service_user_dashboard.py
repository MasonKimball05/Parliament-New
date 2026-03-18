"""
Service Hours User Dashboard Views

User-facing views for submitting and viewing service hours.
Members can submit hours, view their progress, and edit pending submissions.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q
from decimal import Decimal
import logging

from src.models import (
    ServicePeriod, ServiceHoursSubmission, ServiceActivity,
    ServiceFormField, ServiceFieldResponse
)
from src.forms import ServiceHoursSubmissionForm

logger = logging.getLogger('function_calls')


def get_user_service_stats(user, period):
    """
    Calculate service hours statistics for a user in a given period.
    Returns dict with total_hours, approved_hours, pending_hours, expected_hours, progress_percent.
    """
    submissions = ServiceHoursSubmission.objects.filter(
        submitted_by=user,
        period=period
    )

    approved_hours = submissions.filter(status='approved').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    pending_hours = submissions.filter(status='pending').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    rejected_hours = submissions.filter(status='rejected').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    total_hours = approved_hours + pending_hours

    # Get expected hours (check for individual override)
    expected_hours = period.get_member_expected_hours(user)

    # Calculate progress (only approved hours count toward completion)
    if expected_hours > 0:
        progress_percent = min(100, int((approved_hours / expected_hours) * 100))
    else:
        progress_percent = 100 if approved_hours > 0 else 0

    return {
        'approved_hours': approved_hours,
        'pending_hours': pending_hours,
        'rejected_hours': rejected_hours,
        'total_hours': total_hours,
        'expected_hours': expected_hours,
        'progress_percent': progress_percent,
        'remaining_hours': max(Decimal('0'), expected_hours - approved_hours),
    }


@login_required
def user_service_dashboard(request):
    """
    User's personal Service Hours dashboard showing their submissions and progress.
    This is the main entry point for the Service Hours feature.
    """
    user = request.user

    # Get current and recent periods
    today = timezone.now().date()
    active_periods = ServicePeriod.objects.filter(is_active=True).order_by('-start_date')

    # Find current period (or most recent)
    current_period = active_periods.filter(
        start_date__lte=today,
        end_date__gte=today
    ).first()

    if not current_period:
        current_period = active_periods.first()

    # Get stats for current period
    stats = None
    if current_period:
        stats = get_user_service_stats(user, current_period)

    # Get all submissions for this user
    submissions = ServiceHoursSubmission.objects.filter(
        submitted_by=user
    ).select_related('period', 'reviewed_by').order_by('-submitted_at')

    # Check if user is VPP (to show admin link) - case-insensitive
    # Also show on DEBUG mode for dev testing
    from django.conf import settings
    is_vpp = user.is_admin or user.roles.filter(code__iexact='VPP').exists() or settings.DEBUG

    context = {
        'current_period': current_period,
        'active_periods': active_periods,
        'stats': stats,
        'submissions': submissions,
        'is_vpp': is_vpp,
    }

    return render(request, 'service_hours/user_dashboard.html', context)


@login_required
def user_view_submission(request, submission_id):
    """
    User view of their own submission details.
    Shows hours, status, reviewer notes, and custom field responses.
    """
    user = request.user

    submission = get_object_or_404(
        ServiceHoursSubmission,
        submitted_by=user,
        id=submission_id
    )

    # Get custom field responses
    custom_responses = ServiceFieldResponse.objects.filter(
        submission=submission
    ).select_related('field').order_by('field__display_order')

    # Get activity log
    activity_log = ServiceActivity.objects.filter(
        submission=submission
    ).select_related('user').order_by('-timestamp')

    context = {
        'submission': submission,
        'custom_responses': custom_responses,
        'activity_log': activity_log,
        'can_edit': submission.can_edit(),
    }

    return render(request, 'service_hours/user_view_submission.html', context)


@login_required
def submit_service_hours(request):
    """
    Submit new service hours.
    Handles both built-in fields and custom form fields.
    """
    user = request.user

    # Get active periods
    active_periods = ServicePeriod.objects.filter(is_active=True)
    if not active_periods.exists():
        messages.error(request, 'No active service periods available. Please contact the VPP.')
        return redirect('user_service_dashboard')

    # Get custom form fields (exclude built-in fields which are rendered by the Django form)
    custom_fields = ServiceFormField.objects.filter(is_active=True, is_builtin=False).order_by('section', 'display_order')

    if request.method == 'POST':
        form = ServiceHoursSubmissionForm(request.POST, request.FILES)

        if form.is_valid():
            submission = form.save(commit=False)
            submission.submitted_by = user

            # Set initial status based on period's approval requirement
            if submission.period.requires_approval:
                submission.status = 'pending'
            else:
                submission.status = 'approved'
                submission.reviewed_at = timezone.now()

            submission.save()

            # Save custom field responses
            for field in custom_fields:
                field_name = f'custom_{field.field_name}'
                value = request.POST.get(field_name) or request.FILES.get(field_name)

                if value:
                    response = ServiceFieldResponse(submission=submission, field=field)

                    if field.field_type in ['text', 'textarea', 'date', 'select', 'radio']:
                        response.text_value = value
                    elif field.field_type == 'number':
                        try:
                            response.number_value = Decimal(value)
                        except:
                            response.text_value = value
                    elif field.field_type in ['multiselect', 'checkbox']:
                        response.json_value = request.POST.getlist(field_name)
                    elif field.field_type == 'file':
                        response.file_value = value

                    response.save()

            # Log activity
            ServiceActivity.objects.create(
                submission=submission,
                user=user,
                action='created',
                details=f'Submitted {submission.hours} hours for {submission.organization}'
            )

            if submission.status == 'approved':
                messages.success(request, f'Successfully submitted {submission.hours} service hours! (Auto-approved)')
            else:
                messages.success(request, f'Successfully submitted {submission.hours} service hours for approval.')

            return redirect('user_service_dashboard')
    else:
        form = ServiceHoursSubmissionForm()

    context = {
        'form': form,
        'custom_fields': custom_fields,
        'active_periods': active_periods,
    }

    return render(request, 'service_hours/submit_hours.html', context)


@login_required
def edit_service_submission(request, submission_id):
    """
    Edit a pending or rejected submission.
    Only allows editing if submission hasn't been approved yet.
    """
    user = request.user

    submission = get_object_or_404(
        ServiceHoursSubmission,
        submitted_by=user,
        id=submission_id
    )

    if not submission.can_edit():
        messages.error(request, 'This submission cannot be edited because it has been approved.')
        return redirect('user_view_service_submission', submission_id=submission_id)

    # Get custom form fields (exclude built-in fields which are rendered by the Django form)
    custom_fields = ServiceFormField.objects.filter(is_active=True, is_builtin=False).order_by('section', 'display_order')

    # Get existing custom responses
    existing_responses = {
        r.field_id: r for r in ServiceFieldResponse.objects.filter(submission=submission)
    }

    was_rejected = submission.status == 'rejected'

    if request.method == 'POST':
        form = ServiceHoursSubmissionForm(request.POST, request.FILES, instance=submission)

        if form.is_valid():
            submission = form.save(commit=False)

            # If was rejected, resubmit for approval
            if was_rejected and submission.period.requires_approval:
                submission.status = 'pending'
                submission.reviewer_notes = ''  # Clear old rejection notes

            submission.save()

            # Update custom field responses
            for field in custom_fields:
                field_name = f'custom_{field.field_name}'
                value = request.POST.get(field_name) or request.FILES.get(field_name)

                # Get or create response
                response, created = ServiceFieldResponse.objects.get_or_create(
                    submission=submission,
                    field=field,
                    defaults={}
                )

                if value:
                    if field.field_type in ['text', 'textarea', 'date', 'select', 'radio']:
                        response.text_value = value
                    elif field.field_type == 'number':
                        try:
                            response.number_value = Decimal(value)
                        except:
                            response.text_value = value
                    elif field.field_type in ['multiselect', 'checkbox']:
                        response.json_value = request.POST.getlist(field_name)
                    elif field.field_type == 'file':
                        response.file_value = value
                    response.save()
                elif not created:
                    # Clear existing value if empty
                    response.delete()

            # Log activity
            action = 'resubmitted' if was_rejected else 'updated'
            ServiceActivity.objects.create(
                submission=submission,
                user=user,
                action=action,
                details=f'Updated submission to {submission.hours} hours for {submission.organization}'
            )

            if was_rejected:
                messages.success(request, 'Submission updated and resubmitted for approval.')
            else:
                messages.success(request, 'Submission updated successfully.')

            return redirect('user_service_dashboard')
    else:
        form = ServiceHoursSubmissionForm(instance=submission)

    context = {
        'form': form,
        'submission': submission,
        'custom_fields': custom_fields,
        'existing_responses': existing_responses,
        'was_rejected': was_rejected,
    }

    return render(request, 'service_hours/edit_submission.html', context)
