"""
Service Hours User Dashboard Views

User-facing views for submitting and viewing service hours.
Members can submit hours, view their progress, and edit pending submissions.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Sum, Q
from django.conf import settings
from src.tasks import send_email
from decimal import Decimal, InvalidOperation
from datetime import datetime
import uuid
import logging

from src.models import (
    ServicePeriod, ServiceHoursSubmission, ServiceActivity,
    ServiceFormField, ServiceFieldResponse, ServiceHoursAdjustment,
    ServiceMemberExpectation
)
from src.forms import ServiceHoursSubmissionForm
from src.models.users import member_defer

logger = logging.getLogger('function_calls')


def _validated_upload(request, field, value):
    """
    v3.19.7 — validate a custom-field upload, or drop it with a message.

    Both service-hours custom-field writers (create and edit) assigned
    `request.FILES.get(...)` straight to `ServiceFieldResponse.file_value` with
    no validation, while the submission's own `attachment` — the same form, the
    same member, the directory next door — went through
    `ServiceHoursSubmissionForm.clean_attachment`.

    Returns the file if it passes and `None` if it does not, so the caller reads
    as an assignment either way. Dropping rather than failing the POST is
    deliberate and matches the Kai writer: the submission is already saved by
    this point, and refusing a receipt should not discard the hours it belongs
    to. The member is told, so the drop is never silent.

    ⚠️ Shared by both call sites ON PURPOSE. The create path and the edit path
    are the same eight lines twice, and this codebase's recurring failure is a
    rule applied at the site where it was written and not at the site it was
    copied to — the edit path here is exactly that, one release later.
    """
    from django.core.exceptions import ValidationError

    from src.utils.file_validation import validate_uploaded_file

    if not value or not hasattr(value, 'read'):
        return value
    try:
        validate_uploaded_file(value)
    except ValidationError as exc:
        messages.error(request, f'{field.label}: {"; ".join(exc.messages)}')
        return None
    return value


def _notify_vpp_new_submission(submission, is_resubmission=False):
    """Send a notification email to all VPP role holders when a service hour submission is received."""
    from src.models import ParliamentUser
    vpp_users = ParliamentUser.objects.filter(
        roles__code__iexact='VPP',
        member_status='Active',
    ).exclude(email='').filter(email__isnull=False)

    if not vpp_users.exists():
        # Fall back to admins if no VPP is set
        vpp_users = ParliamentUser.objects.filter(
            is_admin=True,
            member_status='Active',
        ).exclude(email='').filter(email__isnull=False)

    if not vpp_users.exists():
        return

    submitted_at = localtime(submission.submitted_at).strftime('%B %d, %Y at %I:%M %p %Z')
    action = 'Resubmitted' if is_resubmission else 'New'
    subject = f"[Service Hours] {action} Submission: {submission.submitted_by.get_display_name()} — {submission.hours} hrs"

    message = f"""{action} service hours submission received.

Member: {submission.submitted_by.get_display_name()}
Hours: {submission.hours}
Organization: {submission.organization}
Description: {submission.description}
Period: {submission.period}
Submitted: {submitted_at}

Review submissions at {getattr(settings, 'SITE_URL', '').rstrip('/')}/service-hours/dashboard/
"""

    recipient_emails = [u.email for u in vpp_users]
    if recipient_emails:
        send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)


def _notify_vpp_new_submissions(submissions, is_resubmission=False):
    """
    v3.29.22 — VPP notification for a multi-date submission. A member
    adding several dates on the "+ Add another date" form creates one
    `ServiceHoursSubmission` row per date (see `submit_service_hours`),
    but experienced it as ONE action — sending N separate emails for that
    would just be spam. Single-date submissions (the common case) still go
    through the original `_notify_vpp_new_submission` unchanged.
    """
    if not submissions:
        return
    if len(submissions) == 1:
        _notify_vpp_new_submission(submissions[0], is_resubmission=is_resubmission)
        return

    from src.models import ParliamentUser
    vpp_users = ParliamentUser.objects.filter(
        roles__code__iexact='VPP',
        member_status='Active',
    ).exclude(email='').filter(email__isnull=False)

    if not vpp_users.exists():
        vpp_users = ParliamentUser.objects.filter(
            is_admin=True,
            member_status='Active',
        ).exclude(email='').filter(email__isnull=False)

    if not vpp_users.exists():
        return

    first = submissions[0]
    total_hours = sum((s.hours for s in submissions), Decimal('0'))
    # v3.29.23: hours can differ per date now, so each date's line names
    # its own hours instead of the message claiming one uniform number
    # ("N dates, X hrs each") that may no longer be true.
    dates_str = ', '.join(
        f"{s.service_date.strftime('%b %d, %Y')} ({s.hours} hrs)"
        for s in sorted(submissions, key=lambda s: s.service_date)
    )
    submitted_at = localtime(first.submitted_at).strftime('%B %d, %Y at %I:%M %p %Z')
    action = 'Resubmitted' if is_resubmission else 'New'
    subject = (
        f"[Service Hours] {action} Submission: {first.submitted_by.get_display_name()} "
        f"— {total_hours} hrs across {len(submissions)} dates"
    )

    message = f"""{action} service hours submission received, for multiple dates.

Member: {first.submitted_by.get_display_name()}
Total Hours: {total_hours} across {len(submissions)} dates
Dates: {dates_str}
Organization: {first.organization}
Description: {first.description}
Period: {first.period}
Submitted: {submitted_at}

Review submissions at {getattr(settings, 'SITE_URL', '').rstrip('/')}/service-hours/dashboard/
"""

    recipient_emails = [u.email for u in vpp_users]
    if recipient_emails:
        send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)


def get_user_service_stats(user, period):
    """
    Calculate service hours statistics for a user in a given period.
    Returns dict with total_hours, approved_hours, pending_hours, expected_hours, progress_percent.
    """
    submissions = ServiceHoursSubmission.objects.filter(
        submitted_by=user,
        period=period
    )

    submitted_approved = submissions.filter(status='approved').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    pending_hours = submissions.filter(status='pending').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    rejected_hours = submissions.filter(status='rejected').aggregate(
        total=Sum('hours')
    )['total'] or Decimal('0')

    # Get manual adjustments
    adjusted_hours = ServiceHoursAdjustment.objects.filter(
        member=user,
        period=period
    ).aggregate(total=Sum('hours'))['total'] or Decimal('0')

    # Total approved = submitted approved + manual adjustments
    approved_hours = submitted_approved + adjusted_hours

    total_hours = approved_hours + pending_hours

    # Get expected hours (check for individual override)
    expected_hours = period.get_member_expected_hours(user)

    # Check if user has a custom expectation and get reason
    expectation_override = None
    try:
        override = ServiceMemberExpectation.objects.get(period=period, member=user)
        expectation_override = {
            'expected_hours': override.expected_hours,
            'reason': override.reason,
            'default_hours': period.default_hours_required,
            'difference': override.expected_hours - period.default_hours_required
        }
    except ServiceMemberExpectation.DoesNotExist:
        pass

    # Calculate progress (only approved hours count toward completion)
    if expected_hours > 0:
        progress_percent = min(100, int((approved_hours / expected_hours) * 100))
    else:
        progress_percent = 100 if approved_hours > 0 else 0

    return {
        'approved_hours': approved_hours,
        'submitted_hours': submitted_approved,
        'adjusted_hours': adjusted_hours,
        'pending_hours': pending_hours,
        'rejected_hours': rejected_hours,
        'total_hours': total_hours,
        'expected_hours': expected_hours,
        'expectation_override': expectation_override,
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
    today = timezone.localdate()   # v3.17.4: calendar date, not UTC
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
    adjustments = []
    if current_period:
        stats = get_user_service_stats(user, current_period)
        # Get adjustments for this user to display on dashboard
        adjustments = ServiceHoursAdjustment.objects.filter(
            member=user,
            period=current_period
        ).select_related('adjusted_by').defer(*member_defer('adjusted_by')).order_by('-created_at')

    # Get all submissions for this user
    submissions = ServiceHoursSubmission.objects.filter(
        submitted_by=user
    ).select_related('period', 'reviewed_by').defer(*member_defer('reviewed_by')).order_by('-submitted_at')

    # Check if user is VPP (to show the admin link) - case-insensitive.
    # No DEBUG shortcut: the VPP admin pages are gated by @vpp_required, so
    # showing the link to every user in DEBUG just diverged from the real gate.
    # (07-22 cleanup, sibling of the vpp_required DEBUG-bypass removal.)
    is_vpp = user.is_admin or user.roles.filter(code__iexact='VPP').exists()

    context = {
        'current_period': current_period,
        'active_periods': active_periods,
        'stats': stats,
        'submissions': submissions,
        'adjustments': adjustments,
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
    ).select_related('user').defer(*member_defer('user')).order_by('-timestamp')

    context = {
        'submission': submission,
        'custom_responses': custom_responses,
        'activity_log': activity_log,
        'can_edit': submission.can_edit(),
    }

    return render(request, 'service_hours/user_view_submission.html', context)


#: v3.29.22 — ceiling on how many EXTRA dates ("+ Add another date") one
#: POST can turn into submission rows, matching the client-side cap in
#: submit_hours.html. Defensive only: nothing about the feature needs a
#: limit this high, it just keeps a crafted POST from creating thousands
#: of rows in one request. 59 + the primary date field's own date = 60.
MAX_EXTRA_SERVICE_DATES = 59


def _extra_service_entries_from_post(request, exclude):
    """
    v3.29.23 — Mason: "instead of assuming that it is x hours exactly each
    day can it instead require the person to enter the number of hours
    they put in for those days?" Each dynamically added row in the
    template now posts a PAIR — `extra_dates[i]` / `extra_hours[i]`,
    aligned by list index (see submit_hours.html's `addDateRow()`) —
    instead of a date that silently borrowed the primary submission's
    hours. Returns a deduplicated list of `(date, Decimal)` pairs.

    A row is dropped if its date is blank/malformed/a duplicate/equal to
    `exclude` (the primary date) — same silent handling as before
    v3.29.23, and for the same reason: there is no future-date or
    period-bounds check on the primary `service_date` field either (see
    `ServiceHoursSubmissionForm`), so this doesn't introduce a new
    restriction. An hours value that's blank, unparsable, or outside the
    `0 < hours <= 24` bound `ServiceHoursSubmissionForm.clean_hours`
    enforces on the primary field is dropped LOUDLY (`messages.warning`,
    naming the date) rather than silently — unlike a blank date, a typed
    hours value is something the member will expect to see reflected, so
    silently discarding it would look like data loss rather than input
    hygiene.
    """
    seen_dates = {exclude}
    result = []
    raw_dates = request.POST.getlist('extra_dates')[:MAX_EXTRA_SERVICE_DATES]
    raw_hours = request.POST.getlist('extra_hours')[:MAX_EXTRA_SERVICE_DATES]
    for raw_date, raw_hour in zip(raw_dates, raw_hours):
        raw_date = (raw_date or '').strip()
        if not raw_date:
            continue
        try:
            parsed_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            continue
        if parsed_date in seen_dates:
            continue

        raw_hour = (raw_hour or '').strip()
        try:
            parsed_hours = Decimal(raw_hour)
        except (InvalidOperation, ValueError):
            messages.warning(
                request,
                f"{parsed_date.strftime('%b %d, %Y')}: no valid hours entered — this date was skipped."
            )
            continue
        if not (0 < parsed_hours <= 24):
            messages.warning(
                request,
                f"{parsed_date.strftime('%b %d, %Y')}: hours must be more than 0 and no more than 24 — this date was skipped."
            )
            continue

        seen_dates.add(parsed_date)
        result.append((parsed_date, parsed_hours))
    return result


def _save_custom_field_responses(request, custom_fields, submission):
    """
    Extracted from `submit_service_hours` unchanged (v3.29.22) so it can
    run once for the primary submission and be reused — via
    `_clone_custom_field_responses` below, NOT by calling this again — for
    each additional date's row. Calling this a second time per extra date
    would re-read `request.FILES`, which is already exhausted after the
    first read for any custom FILE-type field (Django's uploaded-file
    objects are single-read), silently losing the file on every date past
    the first.
    """
    saved = []
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
                except Exception:
                    response.text_value = value
            elif field.field_type in ['multiselect', 'checkbox']:
                response.json_value = request.POST.getlist(field_name)
            elif field.field_type == 'file':
                # v3.19.7 — was assigned straight from request.FILES.
                # See `_validated_upload` for why, and for the one
                # sentence that matters: the submission's own attachment
                # is validated by `ServiceHoursSubmissionForm`, and this
                # field writes to the directory next door.
                response.file_value = _validated_upload(request, field, value)

            response.save()
            saved.append(response)
    return saved


def _clone_custom_field_responses(responses, submission):
    """
    Copy already-saved field VALUES onto a new submission rather than
    re-parsing `request.POST`/`request.FILES` — see the note on
    `_save_custom_field_responses` for why re-parsing per extra date would
    silently drop file uploads. For a file value, pointing the clone's
    `FieldFile.name` at the already-stored path (rather than assigning the
    original `UploadedFile` object again) means the same physical file is
    shared rather than re-uploaded — correct here, since every extra-date
    row is the same event/attachment, just a different day.
    """
    for resp in responses:
        clone = ServiceFieldResponse(
            submission=submission,
            field=resp.field,
            text_value=resp.text_value,
            number_value=resp.number_value,
            json_value=resp.json_value,
        )
        if resp.file_value:
            clone.file_value.name = resp.file_value.name
        clone.save()


@login_required
def submit_service_hours(request):
    """
    Submit new service hours.
    Handles both built-in fields and custom form fields.

    v3.29.22: the form's "+ Add another date" rows let one submission of
    hours/organization/description/attachment be logged against SEVERAL
    dates at once. This creates one `ServiceHoursSubmission` ROW per date
    rather than one row spanning several dates — deliberately, matching
    how the model is used everywhere else in this feature: approval is
    per-row (`status`/`reviewed_by`), editing is per-row
    (`edit_service_submission`), the CSV export is one line per date of
    service, and the activity log is per-row. A single row covering N
    dates would need to redefine all four; N rows sharing everything but
    `service_date` needs none of them touched.
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
            extra_entries = _extra_service_entries_from_post(request, exclude=form.cleaned_data['service_date'])

            submission = form.save(commit=False)
            submission.submitted_by = user
            # v3.29.23 — only assigned when there's actually a second date;
            # a single-date submission keeps batch_id=None, same as before
            # this field existed.
            if extra_entries:
                submission.batch_id = uuid.uuid4()

            # Set initial status based on period's approval requirement
            if submission.period.requires_approval:
                submission.status = 'pending'
            else:
                submission.status = 'approved'
                submission.reviewed_at = timezone.now()

            submission.save()
            all_submissions = [submission]

            # One clone per additional (date, hours) pair, sharing
            # everything except `service_date`/`hours` (and, necessarily,
            # `pk`/`submitted_at`). v3.29.23: hours is now whatever the
            # member entered for that specific date, not a copy of the
            # primary submission's hours.
            for extra_date, extra_hours in extra_entries:
                clone = ServiceHoursSubmission(
                    period=submission.period,
                    submitted_by=user,
                    hours=extra_hours,
                    service_date=extra_date,
                    organization=submission.organization,
                    description=submission.description,
                    status=submission.status,
                    reviewed_at=submission.reviewed_at,
                    batch_id=submission.batch_id,
                )
                if submission.attachment:
                    # Point at the already-uploaded file rather than
                    # re-assigning it — see _clone_custom_field_responses's
                    # docstring for why this is the correct choice here,
                    # not just the cheaper one.
                    clone.attachment.name = submission.attachment.name
                clone.save()
                all_submissions.append(clone)

            # Save custom field responses for the primary submission, then
            # copy the resulting VALUES onto every clone.
            primary_responses = _save_custom_field_responses(request, custom_fields, submission)
            for clone in all_submissions[1:]:
                _clone_custom_field_responses(primary_responses, clone)

            # Log activity — once per submission row, so each date's
            # approval/edit history is independently auditable.
            multi_date_note = f' (multi-date submission, {len(all_submissions)} dates)' if len(all_submissions) > 1 else ''
            for target in all_submissions:
                ServiceActivity.objects.create(
                    submission=target,
                    user=user,
                    action='created',
                    details=f'Submitted {target.hours} hours for {target.organization}{multi_date_note}'
                )

            # Notify VPP once for the whole batch, not once per date.
            if submission.period.requires_approval:
                _notify_vpp_new_submissions(all_submissions)

            total_hours = sum((s.hours for s in all_submissions), Decimal('0'))
            auto_approved = ' (Auto-approved)' if submission.status == 'approved' else ' for approval'
            if len(all_submissions) > 1:
                # v3.29.23: hours can differ per date now, so this no
                # longer claims one uniform per-date number — just the
                # count of dates and the combined total.
                messages.success(
                    request,
                    f'Successfully submitted service hours for {len(all_submissions)} dates '
                    f'({total_hours} hours total){auto_approved}.'
                )
            else:
                messages.success(request, f'Successfully submitted {submission.hours} service hours{auto_approved}.')

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
                        except Exception:
                            response.text_value = value
                    elif field.field_type in ['multiselect', 'checkbox']:
                        response.json_value = request.POST.getlist(field_name)
                    elif field.field_type == 'file':
                        # v3.19.7 — see `_validated_upload`. This is the edit
                        # path; the create path above had the same gap, which is
                        # the usual shape — a rule applied where it was written
                        # and not where it was copied.
                        response.file_value = _validated_upload(request, field, value)
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

            # Notify VPP when a rejected submission is resubmitted for approval
            if was_rejected and submission.status == 'pending':
                _notify_vpp_new_submission(submission, is_resubmission=True)

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
