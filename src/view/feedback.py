"""
Feedback & Support views — a two-in-one page for feature ideas and direct
support tickets, deliberately built like `src/view/bug_report.py` (same
submit/success/my-submissions/tracker/admin shape) so the two systems stay
easy to maintain side by side.

The one place this genuinely differs from bug reports, and the reason it
isn't just BugReport with an extra field: `FeedbackRequest.request_type`
splits the model into a PUBLIC half (feature ideas, visible to the whole
chapter on `feedback_tracker` — a lightweight public roadmap) and a PRIVATE
half (support tickets, visible only to their submitter and the feedback
admin). Every view below has to apply that branch — see each docstring.
"""
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from src.models import FeedbackRequest, ActivityLog
from src.models.users import member_defer
from src.decorators import feedback_admin_required


@login_required
def submit_feedback(request):
    """
    View for submitting a feature idea or a support ticket.
    """
    if request.method == 'POST':
        request_type = request.POST.get('request_type', 'support_ticket')
        if request_type not in dict(FeedbackRequest.REQUEST_TYPES):
            request_type = 'support_ticket'

        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()

        if not title or not description:
            messages.error(request, 'Please provide a title and description.')
            return redirect('feedback_request')

        feedback = FeedbackRequest(
            request_type=request_type,
            title=title,
            description=description,
            page=request.POST.get('page', ''),
            page_url=request.POST.get('page_url', ''),
            feature=request.POST.get('feature', ''),
            priority=request.POST.get('priority', 'medium'),
            submitted_by=request.user,
        )

        if 'attachment' in request.FILES:
            feedback.attachment = request.FILES['attachment']

        feedback.save()

        # Support tickets email the admin; feature ideas deliberately don't
        # (see send_feedback_notification) — they're tracked on the public
        # board instead, which doesn't need a page-the-admin-every-time email.
        send_feedback_notification(feedback, request)

        ActivityLog.log_activity(
            action_type='feedback_submitted',
            user=request.user,
            description=f'{request.user.name} submitted a {feedback.get_request_type_display().lower()}: {feedback.title}',
            request=request,
            object_type='FeedbackRequest',
            object_id=feedback.id,
            object_repr=f'{feedback.get_request_type_display()} #{feedback.id}',
            metadata={
                'request_type': feedback.request_type,
                'priority': feedback.priority,
                'page': feedback.page or '',
            },
        )

        if request_type == 'feature_idea':
            messages.success(request, 'Thanks! Your idea has been posted to the feedback board.')
        else:
            messages.success(request, 'Thanks! Your message has been sent — you\'ll hear back soon.')
        return redirect('feedback_request_success', feedback_id=feedback.id)

    context = {
        'request_types': FeedbackRequest.REQUEST_TYPES,
        'page_choices': FeedbackRequest.PAGE_CHOICES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
    }
    return render(request, 'feedback_request.html', context)


@login_required
def feedback_request_success(request, feedback_id):
    """
    Success page after submitting feedback. Own submissions only, same as
    the bug report success page — no need to branch on request_type here
    since a user's own ticket is always visible to them.
    """
    try:
        feedback = FeedbackRequest.objects.get(id=feedback_id, submitted_by=request.user)
    except FeedbackRequest.DoesNotExist:
        messages.error(request, 'Submission not found.')
        return redirect('home')

    return render(request, 'feedback_request_success.html', {'feedback': feedback})


@login_required
def my_feedback_requests(request):
    """
    View the user's own feedback submissions — both feature ideas and
    support tickets together, private to them (same as my_bug_reports).
    """
    feedback_requests = FeedbackRequest.objects.filter(submitted_by=request.user).order_by('-submitted_at')
    return render(request, 'my_feedback_requests.html', {'feedback_requests': feedback_requests})


@login_required
def feedback_tracker(request):
    """
    Public feature-ideas board. Support tickets are NEVER listed here —
    filtered out at the queryset level, not just hidden in the template,
    since a ticket can carry the kind of personal detail ("I'm locked out of
    my account because...") nobody else in the chapter should see.
    """
    from django.db.models import Case, When, Value, IntegerField

    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')

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

    ideas = FeedbackRequest.objects.filter(request_type='feature_idea').select_related('submitted_by').defer(
        *member_defer('submitted_by')
    ).annotate(status_priority=status_order).order_by('status_priority', '-submitted_at')

    if status_filter:
        ideas = ideas.filter(status=status_filter)
    if priority_filter:
        ideas = ideas.filter(priority=priority_filter)

    idea_base = FeedbackRequest.objects.filter(request_type='feature_idea')
    total_count = idea_base.count()
    new_count = idea_base.filter(status='new').count()
    in_progress_count = idea_base.filter(status__in=['acknowledged', 'in_progress']).count()
    resolved_count = idea_base.filter(status='resolved').count()

    can_manage = str(request.user.user_id) == '73'

    context = {
        'feedback_requests': ideas,
        'status_choices': FeedbackRequest.STATUS_CHOICES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_priority': priority_filter,
        'total_count': total_count,
        'new_count': new_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
        'can_manage': can_manage,
    }
    return render(request, 'feedback_tracker.html', context)


@login_required
def feedback_request_detail(request, feedback_id):
    """
    Detail view. This is the one place the public/private split has real
    teeth: a feature idea is readable by any logged-in member (same as a
    bug report detail page); a support ticket 404s for anyone but its
    submitter or the feedback admin — it never appears on the public board,
    but a guessed/typed URL must not be a second way to read someone else's
    ticket either.
    """
    try:
        feedback = FeedbackRequest.objects.select_related('submitted_by', 'resolved_by').defer(
            *member_defer('submitted_by', 'resolved_by')
        ).get(id=feedback_id)
    except FeedbackRequest.DoesNotExist:
        messages.error(request, 'Submission not found.')
        return redirect('feedback_tracker')

    can_manage = str(request.user.user_id) == '73'
    is_owner = feedback.submitted_by_id == request.user.pk

    if feedback.request_type == 'support_ticket' and not (is_owner or can_manage):
        raise Http404('Submission not found.')

    return render(request, 'feedback_request_detail.html', {
        'feedback': feedback,
        'can_manage': can_manage,
        'is_owner': is_owner,
    })


@feedback_admin_required
def feedback_admin(request):
    """
    Admin page for managing ALL feedback — ideas and tickets together —
    only accessible by the feedback admin (user_id 73).
    """
    from django.db.models import Case, When, Value, IntegerField

    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    priority_filter = request.GET.get('priority', '')

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

    feedback_requests = FeedbackRequest.objects.all().select_related('submitted_by', 'resolved_by').defer(
        *member_defer('submitted_by', 'resolved_by')
    ).annotate(status_priority=status_order).order_by('status_priority', '-submitted_at')

    if status_filter:
        feedback_requests = feedback_requests.filter(status=status_filter)
    if type_filter:
        feedback_requests = feedback_requests.filter(request_type=type_filter)
    if priority_filter:
        feedback_requests = feedback_requests.filter(priority=priority_filter)

    total_count = FeedbackRequest.objects.count()
    idea_count = FeedbackRequest.objects.filter(request_type='feature_idea').count()
    ticket_count = FeedbackRequest.objects.filter(request_type='support_ticket').count()
    new_count = FeedbackRequest.objects.filter(status='new').count()
    in_progress_count = FeedbackRequest.objects.filter(status__in=['acknowledged', 'in_progress']).count()
    resolved_count = FeedbackRequest.objects.filter(status='resolved').count()

    context = {
        'feedback_requests': feedback_requests,
        'status_choices': FeedbackRequest.STATUS_CHOICES,
        'request_types': FeedbackRequest.REQUEST_TYPES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_type': type_filter,
        'current_priority': priority_filter,
        'total_count': total_count,
        'idea_count': idea_count,
        'ticket_count': ticket_count,
        'new_count': new_count,
        'in_progress_count': in_progress_count,
        'resolved_count': resolved_count,
    }
    return render(request, 'feedback_admin.html', context)


@feedback_admin_required
def feedback_admin_update(request, feedback_id):
    """
    Update a feedback request's status and admin notes.
    """
    from django.utils import timezone

    if request.method != 'POST':
        return redirect('feedback_admin')

    try:
        feedback = FeedbackRequest.objects.get(id=feedback_id)
    except FeedbackRequest.DoesNotExist:
        messages.error(request, 'Submission not found.')
        return redirect('feedback_admin')

    new_status = request.POST.get('status')
    admin_notes = request.POST.get('admin_notes', '')

    if new_status and new_status in dict(FeedbackRequest.STATUS_CHOICES):
        old_status = feedback.status
        feedback.status = new_status

        if new_status == 'resolved' and old_status != 'resolved':
            feedback.resolved_at = timezone.now()
            feedback.resolved_by = request.user
        elif new_status != 'resolved' and old_status == 'resolved':
            feedback.resolved_at = None
            feedback.resolved_by = None

    if admin_notes:
        feedback.admin_notes = admin_notes

    feedback.save()
    messages.success(request, f'{feedback.get_request_type_display()} #{feedback_id} updated successfully.')

    next_url = request.POST.get('next', '')
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect('feedback_admin')


def send_feedback_notification(feedback, request):
    """
    Send an email notification when a support ticket is submitted.

    Feature ideas do NOT email — deliberately. A support ticket is the
    "contact me directly" path Mason asked for and every one of those should
    land in his inbox, the same way every bug report already does
    (`send_bug_report_notification`). A feature idea is meant to collect on
    the public board where it's easy to browse; emailing on every one of
    those would just be noise for something that isn't urgent by nature.

    Fails silently (logs, doesn't raise) so a broken mail transport can never
    block someone from getting help — same contract as bug report email.
    """
    if feedback.request_type != 'support_ticket':
        return

    import logging
    logger = logging.getLogger('src')

    try:
        # v3.26.0: settings.EMAIL_BACKEND is the FeatureFlag-gated wrapper;
        # the real transport lives in REAL_EMAIL_BACKEND.
        email_backend = getattr(settings, 'REAL_EMAIL_BACKEND', None) or getattr(settings, 'EMAIL_BACKEND', '')
        is_console_backend = 'console' in email_backend.lower()

        has_smtp = bool(getattr(settings, 'EMAIL_HOST_USER', ''))
        has_brevo = bool(getattr(settings, 'ANYMAIL', {}).get('BREVO_API_KEY', ''))

        if not has_smtp and not has_brevo and not is_console_backend:
            logger.warning("[FEEDBACK EMAIL] No email credentials configured - skipping email notification")
            return

        # Same admin/recipient as bug reports — one inbox, one person to
        # reach, per BugReport's precedent. Deliberately not a new setting.
        admin_email = getattr(settings, 'BUG_REPORT_EMAIL', 'mason.kimball@icloud.com')

        logger.info(f"[FEEDBACK EMAIL] Sending support ticket notification for #{feedback.id} to {admin_email}")

        subject = f"[Support Ticket #{feedback.id}] {feedback.title[:60]}"

        site_url = getattr(settings, 'SITE_URL', request.build_absolute_uri('/'))
        context = {
            'feedback': feedback,
            'site_url': site_url,
            'admin_url': f"{site_url}admin/src/feedbackrequest/{feedback.id}/change/",
        }

        html_message = render_to_string('emails/feedback_notification.html', context)
        plain_message = strip_tags(html_message)

        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[admin_email]
        )
        msg.attach_alternative(html_message, "text/html")
        msg.send(fail_silently=False)

        logger.info(f"[FEEDBACK EMAIL] Successfully sent notification for #{feedback.id}")

    except Exception as e:
        logger.error(f"[FEEDBACK EMAIL] Failed to send notification for #{feedback.id}: {str(e)}")
