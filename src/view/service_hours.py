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
    ServiceFieldResponse, ServiceEvent, Event, Attendance, ActivityLog,
)
from src.forms import ServicePeriodForm, ServiceMemberExpectationForm
from src.decorators import vpp_required
from django.http import JsonResponse
from src.models.users import member_defer

logger = logging.getLogger('function_calls')


@vpp_required
def service_dashboard(request):
    """
    VPP analytics dashboard for service hours.
    Shows statistics, charts, and recent submissions.
    """
    # Get current period
    today = timezone.localdate()   # v3.17.4: calendar date, not UTC
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
        ).defer(*member_defer('submitted_by', 'reviewed_by')).order_by('-submitted_at')[:10]

    context = {
        'current_period': current_period,
        'all_periods': all_periods,
        'stats': stats,
        'member_progress': member_progress,
        'recent_submissions': recent_submissions,
    }

    return render(request, 'service_hours/dashboard.html', context)


@vpp_required
def view_service_submissions(request):
    """
    List all service hour submissions with filtering.
    """
    submissions = ServiceHoursSubmission.objects.select_related(
        'period', 'submitted_by', 'reviewed_by'
    ).defer(*member_defer('submitted_by', 'reviewed_by')).order_by('-submitted_at')

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


@vpp_required
def manage_service_submission(request, submission_id):
    """
    View and approve/reject a single submission.
    """
    submission = get_object_or_404(
        ServiceHoursSubmission.objects.select_related('period', 'submitted_by', 'reviewed_by').defer(*member_defer('submitted_by', 'reviewed_by')),
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
            submission.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'reviewer_notes'])

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
            submission.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'reviewer_notes'])

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
    ).select_related('user').defer(*member_defer('user')).order_by('-timestamp')

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
            submission.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

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
            submission.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'reviewer_notes'])

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


@vpp_required
def export_service_csv(request):
    """
    Export service hours submissions to CSV.
    """
    period_id = request.GET.get('period')

    submissions = ServiceHoursSubmission.objects.select_related(
        'period', 'submitted_by', 'reviewed_by'
    ).defer(*member_defer('submitted_by', 'reviewed_by')).order_by('submitted_by__name', '-submitted_at')

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
            period.save(update_fields=['is_active'])
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


@vpp_required
def manage_member_expectations(request, period_id):
    """
    Manage individual member hour expectations for a period.
    """
    period = get_object_or_404(ServicePeriod, id=period_id)

    # Get existing expectations
    expectations = ServiceMemberExpectation.objects.filter(
        period=period
    ).select_related('member', 'created_by').defer(*member_defer('member', 'created_by')).order_by('member__name')

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
    except Exception:
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
    ).select_related('adjusted_by').defer(*member_defer('adjusted_by')).order_by('-created_at')

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


# ---------------------------------------------------------------------------
# Service Events
# ---------------------------------------------------------------------------

@vpp_required
def service_events_list(request):
    """
    List all service events (upcoming + past) in the VPP dashboard.
    """
    now = timezone.now()

    upcoming = (
        ServiceEvent.objects
        .filter(event__date_time__gte=now, event__is_active=True)
        .select_related('event', 'period')
        .order_by('event__date_time')
    )
    past = (
        ServiceEvent.objects
        .filter(event__date_time__lt=now)
        .select_related('event', 'period')
        .order_by('-event__date_time')[:20]
    )

    return render(request, 'service_hours/service_events.html', {
        'upcoming': upcoming,
        'past': past,
    })


@vpp_required
def create_service_event(request):
    """
    Create a new service event. Also creates the underlying calendar Event
    so it shows up on the chapter calendar.
    """
    periods = ServicePeriod.objects.filter(is_active=True).order_by('-start_date')

    if request.method == 'POST':
        # --- pull form values ---
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        date_str = request.POST.get('date_time', '').strip()
        location = request.POST.get('location', '').strip()
        period_id = request.POST.get('period', '').strip()
        hours_awarded = request.POST.get('hours_awarded', '').strip()

        # Push reminder slots (inherited from existing Event machinery)
        r1_enabled = request.POST.get('reminder_1_enabled') == 'on'
        r1_hours = int(request.POST.get('reminder_1_hours_before', 24) or 24)
        r2_enabled = request.POST.get('reminder_2_enabled') == 'on'
        r2_hours = int(request.POST.get('reminder_2_hours_before', 1) or 1)

        # Email reminder
        email_enabled = request.POST.get('email_reminder_enabled') == 'on'
        email_hours = int(request.POST.get('email_reminder_hours_before', 24) or 24)
        email_subject = request.POST.get('email_reminder_subject', '').strip()
        email_body = request.POST.get('email_reminder_body', '').strip()

        # Validation
        errors = []
        if not title:
            errors.append('Title is required.')
        if not date_str:
            errors.append('Date and time are required.')
        if not period_id:
            errors.append('Service period is required.')
        if not hours_awarded:
            errors.append('Hours awarded is required.')
        if email_enabled and not email_subject:
            errors.append('Email subject is required when email reminder is enabled.')
        if email_enabled and not email_body:
            errors.append('Email body is required when email reminder is enabled.')

        period = None
        if period_id:
            try:
                period = ServicePeriod.objects.get(id=period_id)
            except ServicePeriod.DoesNotExist:
                errors.append('Invalid service period.')

        date_time = None
        if date_str:
            try:
                from django.utils.dateparse import parse_datetime
                date_time = parse_datetime(date_str)
                if date_time is None:
                    raise ValueError
                if timezone.is_naive(date_time):
                    date_time = timezone.make_aware(date_time)
            except (ValueError, TypeError):
                errors.append('Invalid date/time format.')

        try:
            hours_dec = Decimal(hours_awarded)
            if hours_dec <= 0:
                errors.append('Hours awarded must be greater than 0.')
        except Exception:
            errors.append('Hours awarded must be a valid number.')
            hours_dec = None

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'service_hours/create_service_event.html', {
                'periods': periods,
                'post': request.POST,
            })

        # Create underlying calendar Event
        event = Event.objects.create(
            title=title,
            description=description,
            date_time=date_time,
            location=location,
            created_by=request.user,
            requires_attendance=True,
            allow_excuses=False,
            reminder_1_enabled=r1_enabled,
            reminder_1_hours_before=r1_hours,
            reminder_2_enabled=r2_enabled,
            reminder_2_hours_before=r2_hours,
        )

        # Create ServiceEvent
        ServiceEvent.objects.create(
            event=event,
            period=period,
            hours_awarded=hours_dec,
            email_reminder_enabled=email_enabled,
            email_reminder_hours_before=email_hours,
            email_reminder_subject=email_subject,
            email_reminder_body=email_body,
            created_by=request.user,
        )

        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'{request.user.get_display_name()} created service event "{title}" ({hours_dec} hrs, {period.name})',
            request=request,
            object_type='ServiceEvent',
            object_repr=title,
        )

        messages.success(request, f'Service event "{title}" created.')
        return redirect('service_events_list')

    return render(request, 'service_hours/create_service_event.html', {
        'periods': periods,
        'post': {
            'title': '', 'date_time': '', 'location': '', 'description': '',
            'period': '', 'hours_awarded': '',
            'reminder_1_enabled': False, 'reminder_1_hours_before': '',
            'reminder_2_enabled': False, 'reminder_2_hours_before': '',
            'email_reminder_enabled': False, 'email_reminder_hours_before': '',
            'email_reminder_subject': '', 'email_reminder_body': '',
        },
    })


@vpp_required
def edit_service_event(request, service_event_id):
    """
    Edit an existing service event. Propagates changes to the underlying Event too.
    Cannot be edited after hours have been applied.
    """
    se = get_object_or_404(ServiceEvent, id=service_event_id)
    periods = ServicePeriod.objects.filter(is_active=True).order_by('-start_date')

    if se.hours_applied:
        messages.warning(request, 'This service event has been finalized and cannot be edited.')
        return redirect('service_event_detail', service_event_id=se.id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        date_str = request.POST.get('date_time', '').strip()
        location = request.POST.get('location', '').strip()
        period_id = request.POST.get('period', '').strip()
        hours_awarded = request.POST.get('hours_awarded', '').strip()

        r1_enabled = request.POST.get('reminder_1_enabled') == 'on'
        try:
            r1_hours = max(1, int(request.POST.get('reminder_1_hours_before', 24) or 24))
        except (ValueError, TypeError):
            r1_hours = 24
        r2_enabled = request.POST.get('reminder_2_enabled') == 'on'
        try:
            r2_hours = max(1, int(request.POST.get('reminder_2_hours_before', 1) or 1))
        except (ValueError, TypeError):
            r2_hours = 1

        email_enabled = request.POST.get('email_reminder_enabled') == 'on'
        try:
            email_hours = max(1, int(request.POST.get('email_reminder_hours_before', 24) or 24))
        except (ValueError, TypeError):
            email_hours = 24
        email_subject = request.POST.get('email_reminder_subject', '').strip()
        email_body = request.POST.get('email_reminder_body', '').strip()

        errors = []
        if not title:
            errors.append('Title is required.')
        if email_enabled and not email_subject:
            errors.append('Email subject is required when email reminder is enabled.')
        if email_enabled and not email_body:
            errors.append('Email body is required when email reminder is enabled.')

        period = None
        if period_id:
            try:
                period = ServicePeriod.objects.get(id=period_id)
            except ServicePeriod.DoesNotExist:
                errors.append('Invalid service period.')

        date_time = None
        if date_str:
            try:
                from django.utils.dateparse import parse_datetime
                date_time = parse_datetime(date_str)
                if date_time is None:
                    raise ValueError
                if timezone.is_naive(date_time):
                    date_time = timezone.make_aware(date_time)
            except (ValueError, TypeError):
                errors.append('Invalid date/time format.')

        hours_dec = None
        try:
            hours_dec = Decimal(hours_awarded)
            if hours_dec <= 0:
                errors.append('Hours awarded must be greater than 0.')
        except Exception:
            errors.append('Hours awarded must be a valid number.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'service_hours/create_service_event.html', {
                'periods': periods,
                'post': request.POST,
                'editing': se,
            })

        # Update Event
        event = se.event
        # Capture old datetime BEFORE mutating so the reminder-reset check below works.
        old_date_time = event.date_time
        event.title = title
        event.description = description
        if date_time:
            event.date_time = date_time
        event.location = location
        event.reminder_1_enabled = r1_enabled
        event.reminder_1_hours_before = r1_hours
        event.reminder_2_enabled = r2_enabled
        event.reminder_2_hours_before = r2_hours
        event_update_fields = [
            'title', 'description', 'date_time', 'location',
            'reminder_1_enabled', 'reminder_1_hours_before',
            'reminder_2_enabled', 'reminder_2_hours_before',
        ]
        # If the date changed, reset push reminder sent_at so the Celery task
        # re-fires the reminders at the correct new time.
        date_changed = bool(date_time and old_date_time and date_time != old_date_time)
        if date_changed:
            if event.reminder_1_sent_at:
                event.reminder_1_sent_at = None
                event_update_fields.append('reminder_1_sent_at')
            if event.reminder_2_sent_at:
                event.reminder_2_sent_at = None
                event_update_fields.append('reminder_2_sent_at')
        event.save(update_fields=event_update_fields)

        # Update ServiceEvent
        se.period = period or se.period
        se.hours_awarded = hours_dec
        se.email_reminder_enabled = email_enabled
        se.email_reminder_hours_before = email_hours
        se.email_reminder_subject = email_subject
        se.email_reminder_body = email_body
        # If the date changed and the email reminder hasn't fired yet, clear the
        # sent_at timestamp so the Celery task will re-evaluate the new send window.
        se_update_fields = [
            'period', 'hours_awarded',
            'email_reminder_enabled', 'email_reminder_hours_before',
            'email_reminder_subject', 'email_reminder_body',
        ]
        if date_changed and se.email_reminder_sent_at:
            se.email_reminder_sent_at = None
            se_update_fields.append('email_reminder_sent_at')
        se.save(update_fields=se_update_fields)

        messages.success(request, f'Service event "{title}" updated.')
        return redirect('service_event_detail', service_event_id=se.id)

    return render(request, 'service_hours/create_service_event.html', {
        'periods': periods,
        'post': {},
        'editing': se,
    })


@vpp_required
def service_event_detail(request, service_event_id):
    """
    Detail view for a service event: shows event info, attendance roster,
    and (if not yet finalized) the "Finalize & Apply Hours" button.
    """
    se = get_object_or_404(
        ServiceEvent.objects.select_related('event', 'period', 'created_by').defer(*member_defer('created_by')),
        id=service_event_id,
    )
    event = se.event

    # All active members with their attendance status for this event
    members = ParliamentUser.objects.filter(member_status='Active').order_by('name')
    existing = {
        att.user_id: att
        for att in Attendance.objects.filter(event=event, attendance_type='event').select_related('user').defer(*member_defer('user'))
    }
    member_data = []
    for m in members:
        att = existing.get(m.user_id)
        member_data.append({
            'user': m,
            'status': att.status if att else 'pending',
            'marked_at': att.marked_at if att else None,
        })

    present_count = sum(1 for d in member_data if d['status'] == 'present')
    absent_count = sum(1 for d in member_data if d['status'] == 'absent')
    excused_count = sum(1 for d in member_data if d['status'] == 'excused')
    unmarked_count = sum(1 for d in member_data if d['status'] == 'pending')

    can_finalize = not se.hours_applied and present_count > 0

    return render(request, 'service_hours/service_event_detail.html', {
        'se': se,
        'event': event,
        'member_data': member_data,
        'present_count': present_count,
        'absent_count': absent_count,
        'excused_count': excused_count,
        'unmarked_count': unmarked_count,
        'can_finalize': can_finalize,
    })


@vpp_required
def finalize_service_event(request, service_event_id):
    """
    POST-only. Finalizes attendance and creates pre-approved service hours
    submissions for every member marked present.
    """
    if request.method != 'POST':
        return redirect('service_event_detail', service_event_id=service_event_id)

    se = get_object_or_404(ServiceEvent, id=service_event_id)

    if se.hours_applied:
        messages.info(request, 'Hours have already been applied for this event.')
        return redirect('service_event_detail', service_event_id=se.id)

    if se.event.attendance_finalized:
        # Attendance already finalized by someone else — just apply hours
        pass
    else:
        se.event.attendance_finalized = True
        se.event.finalized_by = request.user
        se.event.finalized_at = timezone.now()
        se.event.save(update_fields=['attendance_finalized', 'finalized_by', 'finalized_at'])

    count = se.apply_hours(finalized_by=request.user)

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=(
            f'{request.user.get_display_name()} finalized service event '
            f'"{se.event.title}" — {count} member(s) awarded {se.hours_awarded} hrs each '
            f'toward {se.period.name}.'
        ),
        request=request,
        object_type='ServiceEvent',
        object_repr=se.event.title,
    )

    messages.success(
        request,
        f'Done! {count} member{"s" if count != 1 else ""} awarded {se.hours_awarded} hrs '
        f'toward {se.period.name}.'
    )
    return redirect('service_event_detail', service_event_id=se.id)


def _bulk_save_service_attendance(event, members, present_ids, marked_by):
    """
    Write attendance for a service event in bulk (~4 queries regardless of roster size).

    - present_ids: set of str(user_id) for members to mark present.
    - Upserts present records (update existing, create new).
    - Deletes present marks for any member not in present_ids.
    """
    from django.db import transaction
    now = timezone.now()
    members_list = list(members)

    present_members = [m for m in members_list if str(m.user_id) in present_ids]
    absent_members  = [m for m in members_list if str(m.user_id) not in present_ids]

    with transaction.atomic():
        # 1. Fetch existing records in one query
        existing = {
            att.user_id: att
            for att in Attendance.objects.filter(
                event=event, attendance_type='event', user__in=members_list
            )
        }

        # 2. Split present members into update vs. create
        pks_to_update = [existing[m.user_id].pk for m in present_members if m.user_id in existing]
        to_create     = [m for m in present_members if m.user_id not in existing]

        if pks_to_update:
            Attendance.objects.filter(pk__in=pks_to_update).update(
                status='present', marked_by=marked_by, marked_at=now,
            )
        if to_create:
            Attendance.objects.bulk_create([
                Attendance(
                    event=event, user=m, attendance_type='event',
                    status='present', marked_by=marked_by, marked_at=now,
                )
                for m in to_create
            ])

        # 3. Remove present marks for absent members
        if absent_members:
            Attendance.objects.filter(
                event=event, user__in=absent_members,
                attendance_type='event', status='present',
            ).delete()


def _parse_hours_overrides(members, post_data):
    """Parse per-member hours override fields from POST data into a JSON-safe dict."""
    overrides = {}
    for member in members:
        uid = str(member.user_id)
        raw = post_data.get(f'hours_override_{uid}', '').strip()
        if raw:
            try:
                val = Decimal(raw)
                if val > 0:
                    overrides[uid] = str(val)
            except Exception:
                pass
    return overrides


@vpp_required
def service_event_attendance(request, service_event_id):
    """
    Dedicated attendance page for a service event.

    Simpler than the general mark_event_attendance:
    - Officers just check off who showed up (present).
    - No absent marks, no excuse flow — service attendance is opt-in, not mandatory.
    - Includes the "Finalize & Apply Hours" action so everything stays on one page.
    """
    se = get_object_or_404(
        ServiceEvent.objects.select_related('event', 'period'),
        id=service_event_id,
    )
    event = se.event
    is_read_only = se.hours_applied

    members = list(ParliamentUser.objects.filter(member_status='Active').order_by('name'))

    if request.method == 'POST' and not is_read_only:
        action = request.POST.get('action')
        present_ids = set(request.POST.getlist('present'))

        if action in ('save', 'finalize'):
            # Bulk-write attendance (~4 queries instead of O(n))
            _bulk_save_service_attendance(event, members, present_ids, marked_by=request.user)

            # Persist per-member hours overrides
            se.member_hours_override = _parse_hours_overrides(members, request.POST)
            se.save(update_fields=['member_hours_override'])

        if action == 'save':
            ActivityLog.log_activity(
                action_type='attendance_taken',
                user=request.user,
                description=f'Updated attendance for service event "{event.title}" ({len(present_ids)} present)',
                request=request,
                object_type='ServiceEvent',
                object_repr=event.title,
            )
            messages.success(request, f'Attendance saved — {len(present_ids)} member(s) marked present.')
            return redirect('service_event_attendance', service_event_id=se.id)

        elif action == 'finalize':
            if not present_ids:
                messages.error(request, 'Mark at least one member present before finalizing.')
                return redirect('service_event_attendance', service_event_id=se.id)

            # Finalize underlying event attendance
            if not event.attendance_finalized:
                event.attendance_finalized = True
                event.finalized_by = request.user
                event.finalized_at = timezone.now()
                event.save(update_fields=['attendance_finalized', 'finalized_by', 'finalized_at'])

            count = se.apply_hours(finalized_by=request.user)

            ActivityLog.log_activity(
                action_type='other',
                user=request.user,
                description=(
                    f'{request.user.get_display_name()} finalized service event '
                    f'"{event.title}" — {count} member(s) awarded {se.hours_awarded} hrs '
                    f'toward {se.period.name}.'
                ),
                request=request,
                object_type='ServiceEvent',
                object_repr=event.title,
            )
            messages.success(
                request,
                f'Done! {count} member{"s" if count != 1 else ""} awarded '
                f'{se.hours_awarded} hrs toward {se.period.name}.'
            )
            return redirect('service_event_detail', service_event_id=se.id)

    # Build roster
    existing = {
        att.user_id: att
        for att in Attendance.objects.filter(event=event, attendance_type='event')
    }
    member_data = [
        {
            'user': m,
            'present': existing.get(m.user_id) is not None and existing[m.user_id].status == 'present',
        }
        for m in members
    ]
    present_count = sum(1 for d in member_data if d['present'])

    return render(request, 'service_hours/service_event_attendance.html', {
        'se': se,
        'event': event,
        'member_data': member_data,
        'present_count': present_count,
        'is_read_only': is_read_only,
    })


@vpp_required
def delete_service_event(request, service_event_id):
    """
    POST-only. Deletes a service event (and its underlying calendar Event)
    if hours have not yet been applied.
    """
    if request.method != 'POST':
        return redirect('service_events_list')

    se = get_object_or_404(ServiceEvent, id=service_event_id)

    if se.hours_applied:
        messages.error(request, 'Cannot delete a service event after hours have been applied.')
        return redirect('service_event_detail', service_event_id=se.id)

    title = se.event.title
    se.event.delete()  # cascades to ServiceEvent via OneToOneField
    messages.success(request, f'Service event "{title}" deleted.')
    return redirect('service_events_list')
