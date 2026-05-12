"""
Kai User Dashboard Views

User-facing views for managing their own Kai reports.
Users can view their submitted cases, request closure, and submit new reports.
Also allows accused users to view reports targeting them and request closure.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.db.models import Q
import logging

from src.models import (
    Committee, KaiReport, KaiReportActivity, KaiClosureRequest,
    KaiFormField, KaiReportFieldResponse
)

logger = logging.getLogger('function_calls')

# Outcomes that allow closure/drop requests (case has been addressed)
ELIGIBLE_OUTCOMES = [
    'heard', 'warning_issued', 'mediation', 'sanctions_applied',
    'dismissed', 'thrown_out'
]


@login_required
def user_kai_dashboard(request):
    """
    User's personal Kai dashboard showing their submitted reports
    and reports where they are the accused.
    This is the main entry point when clicking the Kai button on home page.
    """
    user = request.user

    # Get all reports submitted by this user
    submitted_reports = KaiReport.objects.filter(
        submitted_by=user
    ).select_related('targeted_to', 'reviewed_by').order_by('-submitted_at')

    # Get reports where user is the accused (targeted_to)
    # Only show reports where deliberation has progressed past "pending"
    # (i.e., the case has been addressed by the committee)
    accused_reports = KaiReport.objects.filter(
        targeted_to=user
    ).exclude(
        submitted_by=user  # Don't show if user reported themselves
    ).exclude(
        deliberation_outcome='pending'  # Only show once case has been addressed
    ).select_related('submitted_by', 'reviewed_by').order_by('-submitted_at')

    # Get pending closure requests by this user
    pending_closures = KaiClosureRequest.objects.filter(
        requested_by=user,
        status='pending'
    ).select_related('report')

    # Check if user is Kai chair (to show admin link)
    is_kai_chair = False
    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        is_kai_chair = kai_committee.is_chair(user)
    except Committee.DoesNotExist:
        pass

    context = {
        'submitted_reports': submitted_reports,
        'accused_reports': accused_reports,
        'pending_closures': pending_closures,
        'is_kai_chair': is_kai_chair,
    }

    return render(request, 'kai/user_dashboard.html', context)


@login_required
def user_view_report(request, report_id):
    """
    User view of their own submitted report details.
    Shows status, dates, deliberation outcome, and custom field responses.
    Also accessible by the accused user (targeted_to).
    """
    user = request.user

    # Allow access if user is submitter or accused
    report = get_object_or_404(
        KaiReport,
        Q(submitted_by=user) | Q(targeted_to=user),
        id=report_id
    )

    is_submitter = report.submitted_by == user
    is_accused = report.targeted_to == user

    # Get custom field responses
    custom_responses = report.custom_responses.select_related('field').order_by(
        'field__section', 'field__display_order'
    )

    # Get closure requests for this report
    closure_requests = report.closure_requests.all().order_by('-requested_at')

    # Get activity log (limited info for user - not chair notes)
    activity_log = report.activity_log.exclude(
        action__in=['notes_updated', 'committee_notes_updated']
    ).order_by('-timestamp')[:10]

    # Check if user can request closure (available after case is addressed)
    has_pending_request = report.closure_requests.filter(status='pending').exists()
    can_request_closure = (
        report.deliberation_outcome in ELIGIBLE_OUTCOMES and
        not has_pending_request and
        report.status != 'archived'
    )

    # Submitters can also request to drop their case (withdraw complaint)
    # Available even before the case is fully resolved, but not if archived
    can_drop_case = (
        is_submitter and
        not has_pending_request and
        report.status not in ['archived'] and
        report.deliberation_outcome != 'sanctions_applied'  # Can't drop if sanctions already applied
    )

    context = {
        'report': report,
        'custom_responses': custom_responses,
        'closure_requests': closure_requests,
        'activity_log': activity_log,
        'can_request_closure': can_request_closure,
        'can_drop_case': can_drop_case,
        'is_submitter': is_submitter,
        'is_accused': is_accused,
    }

    return render(request, 'kai/user_view_report.html', context)


@login_required
def request_closure(request, report_id):
    """
    User requests closure of their case.
    Available to submitters and accused users after case has been addressed.
    Requires Kai committee approval before case is actually closed.
    """
    user = request.user

    # Allow access if user is submitter or accused
    report = get_object_or_404(
        KaiReport,
        Q(submitted_by=user) | Q(targeted_to=user),
        id=report_id
    )

    is_submitter = report.submitted_by == user

    # Check if closure can be requested
    if report.deliberation_outcome not in ELIGIBLE_OUTCOMES:
        messages.error(
            request,
            'Closure can only be requested for cases that have been heard or resolved.'
        )
        return redirect('user_kai_dashboard')

    # Check for existing pending request
    if report.closure_requests.filter(status='pending').exists():
        messages.warning(request, 'A closure request is already pending for this case.')
        return redirect('user_view_kai_report', report_id=report_id)

    # Check if already archived
    if report.status == 'archived':
        messages.info(request, 'This case has already been archived.')
        return redirect('user_view_kai_report', report_id=report_id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()

        if not reason:
            messages.error(request, 'Please provide a reason for your closure request.')
        else:
            # Create closure request
            closure_request = KaiClosureRequest.objects.create(
                report=report,
                requested_by=user,
                request_type='closure',
                reason=reason
            )

            # Log activity
            role = 'Submitter' if is_submitter else 'Accused party'
            KaiReportActivity.objects.create(
                report=report,
                user=user,
                action='closure_requested',
                details=f'{role} requested case closure. Reason: {reason[:100]}...' if len(reason) > 100 else f'{role} requested case closure. Reason: {reason}'
            )

            # Notify Kai chair(s) via email
            try:
                kai_committee = Committee.objects.get(is_kai_committee=True)
                chair_emails = [
                    chair.email for chair in kai_committee.chairs.all()
                    if chair.email
                ]

                if chair_emails:
                    send_mail(
                        subject=f'[Kai] Closure Request: {report.title}',
                        message=f"""A closure request has been submitted for a Kai report.

Report: {report.title}
Requested by: {user.name} ({role})
Request reason: {reason}

Please review this request in the Kai management system.
""",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=chair_emails,
                        fail_silently=True,
                    )
            except Committee.DoesNotExist:
                pass
            except Exception as e:
                logger.error(f"Failed to send closure request notification: {e}")

            logger.info(f"{user.username} requested closure for Kai report '{report.title}' (ID: {report.id})")
            messages.success(request, 'Your closure request has been submitted and is pending review.')
            return redirect('user_view_kai_report', report_id=report_id)

    context = {
        'report': report,
        'is_submitter': is_submitter,
    }

    return render(request, 'kai/request_closure.html', context)


@login_required
def request_drop_case(request, report_id):
    """
    Submitter requests to drop/withdraw their complaint.
    This is different from closure - it's withdrawing the report entirely.
    Only available to the original submitter, and cannot be done after sanctions applied.
    """
    user = request.user
    report = get_object_or_404(KaiReport, id=report_id, submitted_by=user)

    # Can't drop if sanctions already applied
    if report.deliberation_outcome == 'sanctions_applied':
        messages.error(
            request,
            'This case cannot be dropped as sanctions have already been applied.'
        )
        return redirect('user_view_kai_report', report_id=report_id)

    # Check for existing pending request
    if report.closure_requests.filter(status='pending').exists():
        messages.warning(request, 'A request is already pending for this case.')
        return redirect('user_view_kai_report', report_id=report_id)

    # Check if already archived
    if report.status == 'archived':
        messages.info(request, 'This case has already been archived.')
        return redirect('user_view_kai_report', report_id=report_id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()

        if not reason:
            messages.error(request, 'Please provide a reason for dropping this case.')
        else:
            # Create drop request
            drop_request = KaiClosureRequest.objects.create(
                report=report,
                requested_by=user,
                request_type='drop',
                reason=reason
            )

            # Log activity
            KaiReportActivity.objects.create(
                report=report,
                user=user,
                action='closure_requested',
                details=f'Submitter requested to drop/withdraw case. Reason: {reason[:100]}...' if len(reason) > 100 else f'Submitter requested to drop/withdraw case. Reason: {reason}'
            )

            # Notify Kai chair(s) via email
            try:
                kai_committee = Committee.objects.get(is_kai_committee=True)
                chair_emails = [
                    chair.email for chair in kai_committee.chairs.all()
                    if chair.email
                ]

                if chair_emails:
                    send_mail(
                        subject=f'[Kai] Drop Case Request: {report.title}',
                        message=f"""A request to drop/withdraw a case has been submitted.

Report: {report.title}
Submitted by: {user.name}
Reason for dropping: {reason}

Please review this request in the Kai management system.
""",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=chair_emails,
                        fail_silently=True,
                    )
            except Committee.DoesNotExist:
                pass
            except Exception as e:
                logger.error(f"Failed to send drop case request notification: {e}")

            logger.info(f"{user.username} requested to drop Kai report '{report.title}' (ID: {report.id})")
            messages.success(request, 'Your request to drop this case has been submitted and is pending review.')
            return redirect('user_view_kai_report', report_id=report_id)

    context = {
        'report': report,
    }

    return render(request, 'kai/request_drop.html', context)


@login_required
def user_kai_report_attachment(request, report_id):
    """
    Serve the attachment for a user's own report.
    This ensures users can only access their own report attachments.
    """
    user = request.user

    # Allow access if user is submitter or accused
    report = get_object_or_404(
        KaiReport,
        Q(submitted_by=user) | Q(targeted_to=user),
        id=report_id
    )

    if not report.attachment:
        messages.error(request, 'This report has no attachment.')
        return redirect('user_view_kai_report', report_id=report_id)

    # Redirect to the attachment URL
    return redirect(report.attachment.url)
