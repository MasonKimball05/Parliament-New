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
from django.core.exceptions import ValidationError

from src.models import FeedbackRequest, ActivityLog
from src.models.users import member_defer
from src.decorators import feedback_admin_required
from src.utils.file_validation import validate_uploaded_file
from src.view.bug_report import SCREENSHOT_EXTENSIONS, SCREENSHOT_MAX_BYTES, screenshot_error


#: What a feedback attachment may be (v4.4.2): the bug report screenshot's
#: images, or a PDF — the page's ``accept="image/*,.pdf"``.
ATTACHMENT_EXTENSIONS = SCREENSHOT_EXTENSIONS + ('.pdf',)
ATTACHMENT_MAX_BYTES = SCREENSHOT_MAX_BYTES


def attachment_error(upload):
    """
    None if ``upload`` is an acceptable feedback attachment, else a message for
    the member (v4.4.2, the web form and the app's alike). A photo gets the bug
    report screenshot's checks (``screenshot_error``); a PDF must be one —
    ``validate_uploaded_file`` reads the content, so a renamed file is refused —
    under the same 10 MB.
    """
    name = (upload.name or '').lower()
    if not name.endswith('.pdf'):
        if not name.endswith(SCREENSHOT_EXTENSIONS):
            return 'Attachments must be a photo (PNG, JPEG, WebP or GIF) or a PDF.'
        return screenshot_error(upload)
    if upload.size > ATTACHMENT_MAX_BYTES:
        return 'Attachments can be at most 10 MB.'
    try:
        validate_uploaded_file(upload)
    except ValidationError:
        return "That file isn't a readable PDF."
    finally:
        upload.seek(0)
    return None


class FeedbackInvalid(Exception):
    """The submission can't be saved; `str(e)` says why (v4.4.0)."""


def _text(data, key, default=''):
    """`data[key]` as text. Web forms only send strings; the app's JSON might not."""
    value = data.get(key, default)
    return default if value is None else str(value)


def _choice(value, choices, default):
    """`value` if it's one of the model's choices, else `default` (v4.4.0, as bug reports)."""
    return value if value in {c[0] for c in choices} else default


def _clip(field, value):
    """Trim `value` to the model field's max_length (v4.4.0, as bug reports)."""
    return value[:FeedbackRequest._meta.get_field(field).max_length]


def feedback_choices():
    """The form's choices (v4.4.0: the web page and the app's form)."""
    return {
        'request_types': FeedbackRequest.REQUEST_TYPES,
        'page_choices': FeedbackRequest.PAGE_CHOICES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
    }


def create_feedback(request, data, attachment=None):
    """
    Save a feature idea or support ticket from `data` (the web form's POST,
    or the app's body) and an optional `attachment`, email the admin for a
    ticket, and log it. Raises FeedbackInvalid without a title and details.

    v4.4.0 — moved out of `submit_feedback` so the app's Feedback form
    (src/mobile/views_support.py) saves through the same code. Also checks
    page/priority against the choices and trims text to its column, the
    checks bug reports got in v3.51.6: a hand-edited form (or a bad app
    request) can't 500 on PostgreSQL or store a value the admin pages
    don't know.
    """
    request_type = _choice(_text(data, 'request_type', 'support_ticket'),
                           FeedbackRequest.REQUEST_TYPES, 'support_ticket')

    title = _text(data, 'title').strip()
    description = _text(data, 'description').strip()

    if not title or not description:
        raise FeedbackInvalid('Please provide a title and description.')

    feedback = FeedbackRequest(
        request_type=request_type,
        title=_clip('title', title),
        description=description,
        page=_choice(_text(data, 'page'), FeedbackRequest.PAGE_CHOICES, ''),
        page_url=_clip('page_url', _text(data, 'page_url')),
        feature=_clip('feature', _text(data, 'feature')),
        priority=_choice(_text(data, 'priority', 'medium'), FeedbackRequest.PRIORITY_CHOICES, 'medium'),
        submitted_by=request.user,
    )

    if attachment is not None:
        feedback.attachment = attachment

    feedback.save()

    # Support tickets email the admin; feature ideas deliberately don't
    # (see send_feedback_notification) — they're tracked on the public
    # board instead, which doesn't need a page-the-admin-every-time email.
    send_feedback_notification(feedback, request)

    # v3.29.10: description deliberately carries NO free text — no title,
    # no page/feature detail. `/officers/activity-logs/` (and its CSV
    # export) is readable by every officer and chair via @officer_required,
    # not just the feedback admin (user_id 73). A support ticket's whole
    # design point is "private: submitter + admin only" (see the module
    # docstring), and this description used to interpolate `feedback.title`
    # verbatim — for both types, unconditionally — so a ticket like
    # "CONFIDENTIAL: my roommate is stealing my meds" was one officer-only
    # page away from every chair in the chapter, with the privacy-gated
    # detail page correctly 404ing for them right next to it. Reproduced
    # end to end before fixing: submit as a member, confirm the ticket
    # detail page 404s for an unrelated officer (works correctly), then
    # confirm the same officer reads the title verbatim on
    # /officers/activity-logs/ (it did). `object_repr` already names the
    # type + id without the content, matching what `BugReport`'s own
    # ActivityLog entry does (`send_bug_report_notification` logs
    # `issue_type`/`priority`, never `bug_report.description`) — this
    # brings FeedbackRequest in line with the sibling system's own
    # convention rather than introducing a new one.
    ActivityLog.log_activity(
        action_type='feedback_submitted',
        user=request.user,
        description=f'{request.user.name} submitted a {feedback.get_request_type_display().lower()} '
                     f'(#{feedback.id}, {feedback.priority} priority)',
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
    return feedback


@login_required
def submit_feedback(request):
    """
    View for submitting a feature idea or a support ticket.
    """
    if request.method == 'POST':
        # v4.4.2 — the attachment is checked (it was saved unchecked); a bad
        # one doesn't lose the request, as in the app: it's sent without it.
        attachment = request.FILES.get('attachment')
        problem = attachment_error(attachment) if attachment is not None else None
        try:
            feedback = create_feedback(request, request.POST, None if problem else attachment)
        except FeedbackInvalid as e:
            messages.error(request, str(e))
            return redirect('feedback_request')
        if problem:
            messages.warning(request, f'It was sent without the attachment. {problem}')

        if feedback.request_type == 'feature_idea':
            messages.success(request, 'Thanks! Your idea has been posted to the feedback board.')
        else:
            messages.success(request, 'Thanks! Your message has been sent — you\'ll hear back soon.')
        return redirect('feedback_request_success', feedback_id=feedback.id)

    return render(request, 'feedback_request.html', feedback_choices())


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
    return render(request, 'my_feedback_requests.html', {'feedback_requests': own_feedback_requests(request.user)})


def own_feedback_requests(user):
    """A member's own ideas and tickets, newest first: my_feedback_requests and
    the app's list (src/mobile/views_support_history.py)."""
    return FeedbackRequest.objects.filter(submitted_by=user).order_by('-submitted_at')


def _status_order():
    """In progress first, then acknowledged, new, resolved, won't do, duplicate."""
    from django.db.models import Case, When, Value, IntegerField
    return Case(
        When(status='in_progress', then=Value(1)),
        When(status='acknowledged', then=Value(2)),
        When(status='new', then=Value(3)),
        When(status='resolved', then=Value(4)),
        When(status='wont_fix', then=Value(5)),
        When(status='duplicate', then=Value(6)),
        default=Value(7),
        output_field=IntegerField(),
    )


def feature_ideas(status_filter='', priority_filter=''):
    """
    The ideas board's rows (feedback_tracker, and the app's board in
    src/mobile/views_support_history.py): feature ideas only, never support
    tickets, in progress first, then acknowledged, new, resolved, won't do,
    duplicate; newest first within each.
    """
    ideas = FeedbackRequest.objects.filter(request_type='feature_idea').select_related('submitted_by').defer(
        *member_defer('submitted_by')
    ).annotate(status_priority=_status_order()).order_by('status_priority', '-submitted_at')

    if status_filter:
        ideas = ideas.filter(status=status_filter)
    if priority_filter:
        ideas = ideas.filter(priority=priority_filter)
    return ideas


def feature_idea_counts():
    """The board's four summary cards (unfiltered)."""
    idea_base = FeedbackRequest.objects.filter(request_type='feature_idea')
    return {
        'total_count': idea_base.count(),
        'new_count': idea_base.filter(status='new').count(),
        'in_progress_count': idea_base.filter(status__in=['acknowledged', 'in_progress']).count(),
        'resolved_count': idea_base.filter(status='resolved').count(),
    }


def can_manage_feedback(user):
    """The feedback admin (user_id 73), as feedback_tracker decides it."""
    return str(user.user_id) == '73'


def feedback_visible_to(feedback, user):
    """
    The detail page's (and the attachment's) rule: a feature idea is public
    to every member; a support ticket only to its submitter and the feedback
    admin. Shared by `feedback_request_detail`, `serve_feedback_attachment`
    and the app (src/mobile/views_support_history.py). Anything that isn't
    a feature idea counts as private (the attachment view's fail-closed form).
    """
    if feedback.request_type == 'feature_idea':
        return True
    return feedback.submitted_by_id == user.pk or can_manage_feedback(user)


def feedback_for_detail(feedback_id):
    """The detail page's submission (with who submitted and resolved it), or None."""
    return FeedbackRequest.objects.select_related('submitted_by', 'resolved_by').defer(
        *member_defer('submitted_by', 'resolved_by')
    ).filter(id=feedback_id).first()


def admin_feedback_requests(status_filter='', type_filter='', priority_filter=''):
    """The admin page's rows: ideas and tickets together, in the board's order."""
    feedback_requests = FeedbackRequest.objects.all().select_related('submitted_by', 'resolved_by').defer(
        *member_defer('submitted_by', 'resolved_by')
    ).annotate(status_priority=_status_order()).order_by('status_priority', '-submitted_at')

    if status_filter:
        feedback_requests = feedback_requests.filter(status=status_filter)
    if type_filter:
        feedback_requests = feedback_requests.filter(request_type=type_filter)
    if priority_filter:
        feedback_requests = feedback_requests.filter(priority=priority_filter)
    return feedback_requests


def feedback_admin_counts():
    """The admin page's summary cards (unfiltered, both kinds)."""
    return {
        'total_count': FeedbackRequest.objects.count(),
        'idea_count': FeedbackRequest.objects.filter(request_type='feature_idea').count(),
        'ticket_count': FeedbackRequest.objects.filter(request_type='support_ticket').count(),
        'new_count': FeedbackRequest.objects.filter(status='new').count(),
        'in_progress_count': FeedbackRequest.objects.filter(status__in=['acknowledged', 'in_progress']).count(),
        'resolved_count': FeedbackRequest.objects.filter(status='resolved').count(),
    }


def update_feedback(feedback, user, new_status, admin_notes):
    """
    The admin's status / notes change (`feedback_admin_update`, and the
    app's). An unknown or blank status leaves the status alone; blank notes
    leave the notes alone. Resolving stamps when and by whom; un-resolving
    clears both.
    """
    from django.utils import timezone

    if new_status and new_status in dict(FeedbackRequest.STATUS_CHOICES):
        old_status = feedback.status
        feedback.status = new_status

        if new_status == 'resolved' and old_status != 'resolved':
            feedback.resolved_at = timezone.now()
            feedback.resolved_by = user
        elif new_status != 'resolved' and old_status == 'resolved':
            feedback.resolved_at = None
            feedback.resolved_by = None

    if admin_notes:
        feedback.admin_notes = admin_notes

    feedback.save()


@login_required
def feedback_tracker(request):
    """
    Public feature-ideas board. Support tickets are NEVER listed here —
    filtered out at the queryset level, not just hidden in the template,
    since a ticket can carry the kind of personal detail ("I'm locked out of
    my account because...") nobody else in the chapter should see.
    """
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')

    ideas = feature_ideas(status_filter, priority_filter)
    can_manage = can_manage_feedback(request.user)

    context = {
        'feedback_requests': ideas,
        'status_choices': FeedbackRequest.STATUS_CHOICES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_priority': priority_filter,
        **feature_idea_counts(),
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
    feedback = feedback_for_detail(feedback_id)
    if feedback is None:
        messages.error(request, 'Submission not found.')
        return redirect('feedback_tracker')

    can_manage = can_manage_feedback(request.user)
    is_owner = feedback.submitted_by_id == request.user.pk

    if not feedback_visible_to(feedback, request.user):
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
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    priority_filter = request.GET.get('priority', '')

    context = {
        'feedback_requests': admin_feedback_requests(status_filter, type_filter, priority_filter),
        'status_choices': FeedbackRequest.STATUS_CHOICES,
        'request_types': FeedbackRequest.REQUEST_TYPES,
        'priority_choices': FeedbackRequest.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_type': type_filter,
        'current_priority': priority_filter,
        **feedback_admin_counts(),
    }
    return render(request, 'feedback_admin.html', context)


@feedback_admin_required
def feedback_admin_update(request, feedback_id):
    """
    Update a feedback request's status and admin notes.
    """
    if request.method != 'POST':
        return redirect('feedback_admin')

    try:
        feedback = FeedbackRequest.objects.get(id=feedback_id)
    except FeedbackRequest.DoesNotExist:
        messages.error(request, 'Submission not found.')
        return redirect('feedback_admin')

    update_feedback(feedback, request.user, request.POST.get('status'), request.POST.get('admin_notes', ''))
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
        # v3.35.3: BUG_REPORT_EMAIL, else SECURITY_ALERT_EMAIL (settings.py).
        # This used to fall back to a personal address hard-coded here.
        admin_email = (getattr(settings, 'BUG_REPORT_EMAIL', '')
                       or getattr(settings, 'SECURITY_ALERT_EMAIL', ''))

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
