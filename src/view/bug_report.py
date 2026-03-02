"""
Bug Report views for users to report issues
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from src.models import BugReport


@login_required
def submit_bug_report(request):
    """
    View for submitting a bug report
    """
    if request.method == 'POST':
        # Get form data
        description = request.POST.get('description', '').strip()

        if not description:
            messages.error(request, 'Please provide a description of the issue.')
            return redirect('bug_report')

        # Create the bug report
        bug_report = BugReport(
            description=description,
            issue_type=request.POST.get('issue_type', 'other'),
            page=request.POST.get('page', ''),
            page_url=request.POST.get('page_url', ''),
            feature=request.POST.get('feature', ''),
            priority=request.POST.get('priority', 'medium'),
            steps_to_reproduce=request.POST.get('steps_to_reproduce', ''),
            expected_behavior=request.POST.get('expected_behavior', ''),
            actual_behavior=request.POST.get('actual_behavior', ''),
            browser_info=request.POST.get('browser_info', ''),
            submitted_by=request.user,
        )

        # Handle screenshot upload
        if 'screenshot' in request.FILES:
            bug_report.screenshot = request.FILES['screenshot']

        bug_report.save()

        # Send email notification (if email is configured)
        send_bug_report_notification(bug_report, request)

        messages.success(request, 'Thank you! Your bug report has been submitted successfully.')
        return redirect('bug_report_success', bug_id=bug_report.id)

    # GET request - show the form
    context = {
        'issue_types': BugReport.ISSUE_TYPES,
        'page_choices': BugReport.PAGE_CHOICES,
        'priority_choices': BugReport.PRIORITY_CHOICES,
    }
    return render(request, 'bug_report.html', context)


@login_required
def bug_report_success(request, bug_id):
    """
    Success page after submitting a bug report
    """
    try:
        bug_report = BugReport.objects.get(id=bug_id, submitted_by=request.user)
    except BugReport.DoesNotExist:
        messages.error(request, 'Bug report not found.')
        return redirect('home')

    return render(request, 'bug_report_success.html', {'bug_report': bug_report})


@login_required
def my_bug_reports(request):
    """
    View user's own bug reports
    """
    bug_reports = BugReport.objects.filter(submitted_by=request.user).order_by('-submitted_at')
    return render(request, 'my_bug_reports.html', {'bug_reports': bug_reports})


@login_required
def bug_tracker(request):
    """
    Public bug tracker showing all reported issues and their status.
    Allows filtering by status and issue type.
    """
    from django.db.models import Case, When, Value, IntegerField

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')

    # Custom status ordering: acknowledged/in_progress first, then new, then resolved/others
    status_order = Case(
        When(status='in_progress', then=Value(1)),
        When(status='acknowledged', then=Value(2)),
        When(status='new', then=Value(3)),
        When(status='resolved', then=Value(4)),
        When(status='wont_fix', then=Value(5)),
        When(status='duplicate', then=Value(6)),
        default=Value(7),
        output_field=IntegerField(),
    )

    # Base queryset with custom ordering
    bug_reports = BugReport.objects.all().annotate(
        status_priority=status_order
    ).order_by('status_priority', '-submitted_at')

    # Apply filters
    if status_filter:
        bug_reports = bug_reports.filter(status=status_filter)
    if type_filter:
        bug_reports = bug_reports.filter(issue_type=type_filter)

    # Get counts for summary
    total_count = BugReport.objects.count()
    new_count = BugReport.objects.filter(status='new').count()
    in_progress_count = BugReport.objects.filter(status__in=['acknowledged', 'in_progress']).count()
    resolved_count = BugReport.objects.filter(status='resolved').count()

    # Check if user can manage bugs (user_id 73)
    can_manage = str(request.user.user_id) == '73'

    context = {
        'bug_reports': bug_reports,
        'status_choices': BugReport.STATUS_CHOICES,
        'issue_types': BugReport.ISSUE_TYPES,
        'current_status': status_filter,
        'current_type': type_filter,
        'total_count': total_count,
        'new_count': new_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
        'can_manage': can_manage,
    }
    return render(request, 'bug_tracker.html', context)


@login_required
def bug_report_detail(request, bug_id):
    """
    View details of a specific bug report
    """
    try:
        bug_report = BugReport.objects.select_related('submitted_by', 'resolved_by').get(id=bug_id)
    except BugReport.DoesNotExist:
        messages.error(request, 'Bug report not found.')
        return redirect('bug_tracker')

    # Check if user can manage bugs (user_id 73)
    can_manage = str(request.user.user_id) == '73'

    return render(request, 'bug_report_detail.html', {
        'bug_report': bug_report,
        'can_manage': can_manage,
    })


# Bug Report Admin - Only accessible by user_id 73
BUG_ADMIN_USER_ID = '73'

def bug_admin_required(view_func):
    """Decorator to restrict access to bug admin (user_id 73)"""
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if str(request.user.user_id) != BUG_ADMIN_USER_ID:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('bug_tracker')
        return view_func(request, *args, **kwargs)
    return wrapper


@bug_admin_required
def bug_admin(request):
    """
    Admin page for managing bug reports - only accessible by user_id 73
    """
    from django.utils import timezone
    from django.db.models import Case, When, Value, IntegerField

    # Get filter parameters
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    priority_filter = request.GET.get('priority', '')

    # Custom status ordering: acknowledged/in_progress first, then new, then resolved/others
    status_order = Case(
        When(status='in_progress', then=Value(1)),
        When(status='acknowledged', then=Value(2)),
        When(status='new', then=Value(3)),
        When(status='resolved', then=Value(4)),
        When(status='wont_fix', then=Value(5)),
        When(status='duplicate', then=Value(6)),
        default=Value(7),
        output_field=IntegerField(),
    )

    # Base queryset with custom ordering
    bug_reports = BugReport.objects.all().select_related('submitted_by', 'resolved_by').annotate(
        status_priority=status_order
    ).order_by('status_priority', '-submitted_at')

    # Apply filters
    if status_filter:
        bug_reports = bug_reports.filter(status=status_filter)
    if type_filter:
        bug_reports = bug_reports.filter(issue_type=type_filter)
    if priority_filter:
        bug_reports = bug_reports.filter(priority=priority_filter)

    # Get counts for summary
    total_count = BugReport.objects.count()
    new_count = BugReport.objects.filter(status='new').count()
    in_progress_count = BugReport.objects.filter(status__in=['acknowledged', 'in_progress']).count()
    resolved_count = BugReport.objects.filter(status='resolved').count()

    context = {
        'bug_reports': bug_reports,
        'status_choices': BugReport.STATUS_CHOICES,
        'issue_types': BugReport.ISSUE_TYPES,
        'priority_choices': BugReport.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_type': type_filter,
        'current_priority': priority_filter,
        'total_count': total_count,
        'new_count': new_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
    }
    return render(request, 'bug_admin.html', context)


@bug_admin_required
def bug_admin_update(request, bug_id):
    """
    Update a bug report's status and admin notes
    """
    from django.utils import timezone

    if request.method != 'POST':
        return redirect('bug_admin')

    try:
        bug_report = BugReport.objects.get(id=bug_id)
    except BugReport.DoesNotExist:
        messages.error(request, 'Bug report not found.')
        return redirect('bug_admin')

    # Get form data
    new_status = request.POST.get('status')
    admin_notes = request.POST.get('admin_notes', '')

    # Update the bug report
    if new_status and new_status in dict(BugReport.STATUS_CHOICES):
        old_status = bug_report.status
        bug_report.status = new_status

        # If marking as resolved, set resolved_at and resolved_by
        if new_status == 'resolved' and old_status != 'resolved':
            bug_report.resolved_at = timezone.now()
            bug_report.resolved_by = request.user
        # If un-resolving, clear resolved fields
        elif new_status != 'resolved' and old_status == 'resolved':
            bug_report.resolved_at = None
            bug_report.resolved_by = None

    if admin_notes:
        bug_report.admin_notes = admin_notes

    bug_report.save()
    messages.success(request, f'Bug #{bug_id} updated successfully.')

    # Redirect back to the same page or admin
    next_url = request.POST.get('next', '')
    if next_url:
        return redirect(next_url)
    return redirect('bug_admin')


def send_bug_report_notification(bug_report, request):
    """
    Send email notification when a bug report is submitted.
    Fails silently if email is not configured.
    """
    import logging
    logger = logging.getLogger('src')

    try:
        # Check if email is properly configured
        if not getattr(settings, 'EMAIL_HOST_USER', None):
            logger.warning("[BUG REPORT EMAIL] EMAIL_HOST_USER not configured - skipping email notification")
            return  # Email not configured, skip silently

        # Get admin email (you can configure this in settings)
        admin_email = getattr(settings, 'BUG_REPORT_EMAIL', 'mason.kimball@icloud.com')

        logger.info(f"[BUG REPORT EMAIL] Sending notification for bug #{bug_report.id} to {admin_email}")

        # Build the email
        subject = f"[Bug Report #{bug_report.id}] {bug_report.get_issue_type_display()}: {bug_report.description[:50]}"

        # Context for email template
        site_url = getattr(settings, 'SITE_URL', request.build_absolute_uri('/'))
        context = {
            'bug_report': bug_report,
            'site_url': site_url,
            'admin_url': f"{site_url}admin/src/bugreport/{bug_report.id}/change/",
        }

        # Render email templates
        html_message = render_to_string('emails/bug_report_notification.html', context)
        plain_message = strip_tags(html_message)

        # Send the email
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[admin_email]
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send(fail_silently=False)

        logger.info(f"[BUG REPORT EMAIL] Successfully sent notification for bug #{bug_report.id}")

    except Exception as e:
        # Log the error but don't break bug submission
        import logging
        logger = logging.getLogger('src')
        logger.error(f"[BUG REPORT EMAIL] Failed to send notification for bug #{bug_report.id}: {str(e)}")
