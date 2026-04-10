"""
Service Hours Officer Dashboard Views

VPP-facing views for managing service hours submissions, periods, and member expectations.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Sum, Count, Q
from django.http import HttpResponse
from decimal import Decimal
import csv
import logging

from src.models import (
    ServicePeriod, ServiceMemberExpectation, ServiceHoursSubmission,
    ServiceActivity, ParliamentUser, ServiceHoursAdjustment,
    ServiceFieldResponse
)
from src.forms import ServicePeriodForm, ServiceMemberExpectationForm
from src.decorators import vpp_required
from django.http import JsonResponse

logger = logging.getLogger('function_calls')


@login_required
@vpp_required
def service_dashboard(request):
    """
    VPP analytics dashboard for service hours.
    Shows statistics, charts, and recent submissions.
    """
    # Get current period
    today = timezone.now().date()
    current_period = ServicePeriod.objects.filter(
        is_active=True,
        start_date__lte=today,
        end_date__gte=today
    ).first()

    if not current_period:
        current_period = ServicePeriod.objects.filter(is_active=True).order_by('-start_date').first()

    # Get all periods for selector
    all_periods = ServicePeriod.objects.all().order_by('-start_date')

    # Allow period selection via query param
    selected_period_id = request.GET.get('period')
    if selected_period_id:
        selected_period = ServicePeriod.objects.filter(id=selected_period_id).first()
        if selected_period:
            current_period = selected_period

    stats = {}
    member_progress = []
    recent_submissions = []

    if current_period:
        # Submission stats for current period
        submissions = ServiceHoursSubmission.objects.filter(period=current_period)

        stats = {
            'total_submissions': submissions.count(),
            'pending_count': submissions.filter(status='pending').count(),
            'approved_count': submissions.filter(status='approved').count(),
            'rejected_count': submissions.filter(status='rejected').count(),
            'total_approved_hours': submissions.filter(status='approved').aggregate(
                total=Sum('hours')
            )['total'] or Decimal('0'),
            'total_pending_hours': submissions.filter(status='pending').aggregate(
                total=Sum('hours')
            )['total'] or Decimal('0'),
        }

        # Member progress for current period
        active_members = ParliamentUser.objects.filter(
            member_status='Active'
        ).exclude(member_type='Advisor')

        for member in active_members:
            approved_hours = submissions.filter(
                submitted_by=member,
                status='approved'
            ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

            pending_hours = submissions.filter(
                submitted_by=member,
                status='pending'
            ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

            # Include manual adjustments in approved hours
            adjusted_hours = ServiceHoursAdjustment.objects.filter(
                period=current_period,
                member=member
            ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

            total_approved = approved_hours + adjusted_hours

            expected_hours = current_period.get_member_expected_hours(member)

            if expected_hours > 0:
                progress_percent = min(100, int((total_approved / expected_hours) * 100))
            else:
                progress_percent = 100 if total_approved > 0 else 0

            member_progress.append({
                'member': member,
                'approved_hours': total_approved,
                'submitted_hours': approved_hours,
                'adjusted_hours': adjusted_hours,
                'pending_hours': pending_hours,
                'expected_hours': expected_hours,
                'progress_percent': progress_percent,
                'completed': total_approved >= expected_hours,
            })

        # Sort by progress (lowest first)
        member_progress.sort(key=lambda x: x['progress_percent'])

        # Recent submissions
        recent_submissions = submissions.select_related(
            'submitted_by', 'reviewed_by'
        ).order_by('-submitted_at')[:10]

    context = {
        'current_period': current_period,
        'all_periods': all_periods,
        'stats': stats,
        'member_progress': member_progress,
        'recent_submissions': recent_submissions,
    }

    return render(request, 'service_hours/dashboard.html', context)


@login_required
@vpp_required
def view_service_submissions(request):
    """
    List all service hour submissions with filtering.
    """
    submissions = ServiceHoursSubmission.objects.select_related(
        'period', 'submitted_by', 'reviewed_by'
    ).order_by('-submitted_at')

    # Filters
    period_id = request.GET.get('period')
    status = request.GET.get('status')
    member_id = request.GET.get('member')

    if period_id:
        submissions = submissions.filter(period_id=period_id)
    if status:
        submissions = submissions.filter(status=status)
    if member_id:
        submissions = submissions.filter(submitted_by_id=member_id)

    # Get filter options
    all_periods = ServicePeriod.objects.all().order_by('-start_date')
    all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    context = {
        'submissions': submissions,
        'all_periods': all_periods,
        'all_members': all_members,
        'selected_period': period_id,
        'selected_status': status,
        'selected_member': member_id,
    }

    return render(request, 'service_hours/view_submissions.html', context)


@login_required
@vpp_required
def manage_service_submission(request, submission_id):
    """
    View and approve/reject a single submission.
    """
    submission = get_object_or_404(
        ServiceHoursSubmission.objects.select_related('period', 'submitted_by', 'reviewed_by'),
        id=submission_id
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        notes = request.POST.get('reviewer_notes', '').strip()

        if action == 'approve':
            submission.status = 'approved'
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()
            submission.reviewer_notes = notes
            submission.save()

            ServiceActivity.objects.create(
                submission=submission,
                user=request.user,
                action='approved',
                details=notes or 'Approved by VPP'
            )

            messages.success(request, f'Approved {submission.hours} hours for {submission.submitted_by.name}.')

        elif action == 'reject':
            if not notes:
                messages.error(request, 'Please provide a reason for rejection.')
                return redirect('manage_service_submission', submission_id=submission_id)

            submission.status = 'rejected'
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()
            submission.reviewer_notes = notes
            submission.save()

            ServiceActivity.objects.create(
                submission=submission,
                user=request.user,
                action='rejected',
                details=notes
            )

            messages.warning(request, f'Rejected submission from {submission.submitted_by.name}.')

        return redirect('view_service_submissions')

    # Get activity log
    activity_log = ServiceActivity.objects.filter(
        submission=submission
    ).select_related('user').order_by('-timestamp')

    # Get custom field responses
    custom_responses = ServiceFieldResponse.objects.filter(
        submission=submission
    ).select_related('field').order_by('field__display_order')

    context = {
        'submission': submission,
        'activity_log': activity_log,
        'custom_responses': custom_responses,
    }

    return render(request, 'service_hours/manage_submission.html', context)


@login_required
@vpp_required
def bulk_actions_service(request):
    """
    Handle bulk approve/reject actions.
    """
    if request.method != 'POST':
        return redirect('view_service_submissions')

    action = request.POST.get('bulk_action')
    submission_ids = request.POST.getlist('submission_ids')

    if not submission_ids:
        messages.error(request, 'No submissions selected.')
        return redirect('view_service_submissions')

    submissions = ServiceHoursSubmission.objects.filter(
        id__in=submission_ids,
        status='pending'
    )

    count = 0
    for submission in submissions:
        if action == 'approve':
            submission.status = 'approved'
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()
            submission.save()

            ServiceActivity.objects.create(
                submission=submission,
                user=request.user,
                action='approved',
                details='Bulk approved'
            )
            count += 1

        elif action == 'reject':
            submission.status = 'rejected'
            submission.reviewed_by = request.user
            submission.reviewed_at = timezone.now()
            submission.reviewer_notes = 'Rejected via bulk action'
            submission.save()

            ServiceActivity.objects.create(
                submission=submission,
                user=request.user,
                action='rejected',
                details='Bulk rejected'
            )
            count += 1

    if action == 'approve':
        messages.success(request, f'Approved {count} submission(s).')
    elif action == 'reject':
        messages.warning(request, f'Rejected {count} submission(s).')

    return redirect('view_service_submissions')


@login_required
@vpp_required
def export_service_csv(request):
    """
    Export service hours submissions to CSV.
    """
    period_id = request.GET.get('period')

    submissions = ServiceHoursSubmission.objects.select_related(
        'period', 'submitted_by', 'reviewed_by'
    ).order_by('submitted_by__name', '-submitted_at')

    if period_id:
        submissions = submissions.filter(period_id=period_id)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="service_hours_export.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Member', 'Period', 'Hours', 'Service Date', 'Organization',
        'Description', 'Status', 'Submitted At', 'Reviewed By', 'Reviewed At'
    ])

    for submission in submissions:
        writer.writerow([
            submission.submitted_by.name,
            submission.period.name,
            submission.hours,
            submission.service_date,
            submission.organization,
            submission.description,
            submission.get_status_display(),
            localtime(submission.submitted_at).strftime('%Y-%m-%d %H:%M'),
            submission.reviewed_by.name if submission.reviewed_by else '',
            localtime(submission.reviewed_at).strftime('%Y-%m-%d %H:%M') if submission.reviewed_at else '',
        ])

    return response


@login_required
@vpp_required
def manage_service_periods(request):
    """
    Manage service periods (CRUD).
    """
    periods = ServicePeriod.objects.all().order_by('-start_date')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            form = ServicePeriodForm(request.POST)
            if form.is_valid():
                period = form.save(commit=False)
                period.created_by = request.user
                period.save()
                messages.success(request, f'Created period: {period.name}')
                return redirect('manage_service_periods')
            else:
                messages.error(request, 'Please correct the errors below.')
        elif action == 'delete':
            period_id = request.POST.get('period_id')
            period = get_object_or_404(ServicePeriod, id=period_id)
            period_name = period.name
            period.delete()
            messages.success(request, f'Deleted period: {period_name}')
            return redirect('manage_service_periods')
        elif action == 'toggle_active':
            period_id = request.POST.get('period_id')
            period = get_object_or_404(ServicePeriod, id=period_id)
            period.is_active = not period.is_active
            period.save()
            status = 'activated' if period.is_active else 'deactivated'
            messages.success(request, f'{period.name} {status}.')
            return redirect('manage_service_periods')
    else:
        form = ServicePeriodForm()

    context = {
        'periods': periods,
        'form': form,
    }

    return render(request, 'service_hours/manage_periods.html', context)


@login_required
@vpp_required
def edit_service_period(request, period_id):
    """
    Edit a service period.
    """
    period = get_object_or_404(ServicePeriod, id=period_id)

    if request.method == 'POST':
        form = ServicePeriodForm(request.POST, instance=period)
        if form.is_valid():
            form.save()
            messages.success(request, f'Updated period: {period.name}')
            return redirect('manage_service_periods')
    else:
        form = ServicePeriodForm(instance=period)

    context = {
        'period': period,
        'form': form,
    }

    return render(request, 'service_hours/edit_period.html', context)


@login_required
@vpp_required
def manage_member_expectations(request, period_id):
    """
    Manage individual member hour expectations for a period.
    """
    period = get_object_or_404(ServicePeriod, id=period_id)

    # Get existing expectations
    expectations = ServiceMemberExpectation.objects.filter(
        period=period
    ).select_related('member', 'created_by').order_by('member__name')

    # Get members without expectations
    members_with_expectations = expectations.values_list('member_id', flat=True)
    available_members = ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(
        user_id__in=members_with_expectations
    ).exclude(
        member_type='Advisor'
    ).order_by('name')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            member_id = request.POST.get('member')
            expected_hours = request.POST.get('expected_hours')
            reason = request.POST.get('reason', '').strip()

            if member_id and expected_hours:
                member = get_object_or_404(ParliamentUser, user_id=member_id)

                ServiceMemberExpectation.objects.create(
                    period=period,
                    member=member,
                    expected_hours=Decimal(expected_hours),
                    reason=reason,
                    created_by=request.user
                )
                messages.success(request, f'Set {expected_hours} hours for {member.name}.')

        elif action == 'delete':
            expectation_id = request.POST.get('expectation_id')
            expectation = get_object_or_404(ServiceMemberExpectation, id=expectation_id, period=period)
            member_name = expectation.member.name
            expectation.delete()
            messages.success(request, f'Removed custom expectation for {member_name}. They will use the default ({period.default_hours_required} hours).')

        return redirect('manage_member_expectations', period_id=period_id)

    context = {
        'period': period,
        'expectations': expectations,
        'available_members': available_members,
    }

    return render(request, 'service_hours/manage_expectations.html', context)


@login_required
@vpp_required
def add_service_adjustment(request):
    """
    Add a manual service hours adjustment for a member.
    VPP/admins can grant or deduct hours with a required reason.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    member_id = request.POST.get('member_id')
    period_id = request.POST.get('period_id')
    hours = request.POST.get('hours')
    reason = request.POST.get('reason', '').strip()

    # Validate required fields
    if not all([member_id, period_id, hours]):
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    if not reason:
        return JsonResponse({'success': False, 'error': 'Reason is required for hour adjustments'}, status=400)

    try:
        hours = Decimal(hours)
    except:
        return JsonResponse({'success': False, 'error': 'Invalid hours value'}, status=400)

    if hours == 0:
        return JsonResponse({'success': False, 'error': 'Hours cannot be zero'}, status=400)

    member = get_object_or_404(ParliamentUser, user_id=member_id)
    period = get_object_or_404(ServicePeriod, id=period_id)

    # Create the adjustment
    adjustment = ServiceHoursAdjustment.objects.create(
        period=period,
        member=member,
        hours=hours,
        reason=reason,
        adjusted_by=request.user
    )

    # Log activity
    action_word = "granted" if hours > 0 else "deducted"
    logger.info(f"Service hours adjustment: {request.user.name} {action_word} {abs(hours)} hrs to {member.name} for {period.name}")

    return JsonResponse({
        'success': True,
        'message': f'Successfully {action_word} {abs(hours)} hours {"to" if hours > 0 else "from"} {member.name}.',
        'adjustment': {
            'id': adjustment.id,
            'hours': float(adjustment.hours),
            'reason': adjustment.reason,
            'member_name': member.name,
            'adjusted_by': request.user.name,
            'created_at': localtime(adjustment.created_at).strftime('%b %d, %Y %I:%M %p')
        }
    })


@login_required
@vpp_required
def delete_service_adjustment(request, adjustment_id):
    """
    Delete a service hours adjustment.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    adjustment = get_object_or_404(ServiceHoursAdjustment, id=adjustment_id)
    member_name = adjustment.member.name
    hours = adjustment.hours

    adjustment.delete()

    action_word = "granted" if hours > 0 else "deducted"
    logger.info(f"Service hours adjustment deleted: {abs(hours)} hrs {action_word} to {member_name} removed by {request.user.name}")

    return JsonResponse({
        'success': True,
        'message': f'Adjustment removed successfully.'
    })


@login_required
@vpp_required
def get_member_adjustments(request, period_id, member_id):
    """
    Get all adjustments for a specific member in a period.
    Returns JSON for AJAX requests.
    """
    period = get_object_or_404(ServicePeriod, id=period_id)
    member = get_object_or_404(ParliamentUser, user_id=member_id)

    adjustments = ServiceHoursAdjustment.objects.filter(
        period=period,
        member=member
    ).select_related('adjusted_by').order_by('-created_at')

    adjustments_data = [{
        'id': adj.id,
        'hours': float(adj.hours),
        'reason': adj.reason,
        'adjusted_by': adj.adjusted_by.name if adj.adjusted_by else 'System',
        'created_at': adj.created_at.strftime('%b %d, %Y %I:%M %p')
    } for adj in adjustments]

    total_adjusted = sum(adj.hours for adj in adjustments)

    return JsonResponse({
        'success': True,
        'adjustments': adjustments_data,
        'total_adjusted': float(total_adjusted),
        'member_name': member.name
    })
