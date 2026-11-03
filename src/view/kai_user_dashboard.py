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
from django.conf import settings
from src.tasks import send_email
from django.db.models import Q
import logging

from src.models.users import member_defer
from src.models import (
    Committee, KaiReport, KaiReportActivity, KaiClosureRequest,
    KaiFormField, KaiReportFieldResponse, ActivityLog, KaiAppeal, KaiRecusal
)

logger = logging.getLogger('function_calls')

# Outcomes that allow closure/drop requests (case has been addressed)
ELIGIBLE_OUTCOMES = [
    'heard', 'warning_issued', 'mediation', 'sanctions_applied',
    'dismissed', 'thrown_out'
]


def member_dashboard(user):
    """
    What the member's Kai dashboard lists for ``user``: the cases they filed,
    the cases they're named in (once addressed), their pending requests, the
    cases they stand in on, and whether they may open the review list.

    The one definition of the dashboard's who-sees-what, shared by
    ``user_kai_dashboard`` and the app (``src/mobile/views_kai.py``).
    """
    # Get all reports submitted by this user
    submitted_reports = KaiReport.objects.filter(
        submitted_by=user
    ).select_related('targeted_to').defer(*member_defer('targeted_to')).order_by('-submitted_at')  # v3.17.3: reviewed_by unused by user_dashboard.html

    # Get reports where user is the accused (targeted_to)
    # Only show reports where deliberation has progressed past "pending"
    # (i.e., the case has been addressed by the committee)
    accused_reports = KaiReport.objects.filter(
        targeted_to=user
    ).exclude(
        submitted_by=user  # Don't show if user reported themselves
    ).exclude(
        deliberation_outcome='pending'  # Only show once case has been addressed
    ).select_related('submitted_by').defer(*member_defer('submitted_by')).order_by('-submitted_at')  # v3.17.3: reviewed_by unused

    # Get pending closure requests by this user
    pending_closures = KaiClosureRequest.objects.filter(
        requested_by=user,
        status='pending'
    )  # v3.17.3: select_related('report') removed — the template never reads it

    # Link to the review dashboard. ⚠️ 09-24-26 — was `kai_committee.is_chair(user)`,
    # which hid the link from every `KaiMemberPermission` grantee (and showed it
    # to exec-board members via `Committee.is_chair`'s shortcut). Now the same
    # predicate the list page enforces. Admins get it only with a real grant
    # or an active break-glass.
    can_view_kai_reports = user.can_access_kai

    # v3.18.0 — cases this member is STANDING IN on (bylaws §§ vi-ix).
    #
    # A stand-in may hold no Kai committee grant of their own: they were
    # appointed to fill one vacated seat on one case. Without this section they
    # would have to be handed the URL — the appointment would be real and
    # invisible. This is their way in.
    standin_recusals = list(
        KaiRecusal.objects
        .filter(replacement=user)
        .select_related('report', 'user')
        .defer(*member_defer('user'))
        .order_by('-appointed_at', '-created_at')
    )
    # v4.4.2: a party's seat (the accused, or the submitter, on the committee)
    # isn't named here. The case page names them only to those with the
    # identity flags, and a stand-in may hold none.
    for rec in standin_recusals:
        rec.seat_label = 'a recused member' if rec.reason in ('accused', 'submitter') else rec.user.name

    return {
        'submitted_reports': submitted_reports,
        'accused_reports': accused_reports,
        'pending_closures': pending_closures,
        'can_view_kai_reports': can_view_kai_reports,
        'standin_recusals': standin_recusals,
    }


@login_required
def user_kai_dashboard(request):
    """
    User's personal Kai dashboard showing their submitted reports
    and reports where they are the accused.
    This is the main entry point when clicking the Kai button on home page.
    """
    return render(request, 'kai/user_dashboard.html', member_dashboard(request.user))


def member_report_context(report, user):
    """
    Everything the member's view of one case shows ``user`` (its submitter or
    the accused): custom responses, requests, the redacted activity feed, the
    closure / drop / appeal eligibility and the appeal window.

    Shared by ``user_view_report`` and the app (``src/mobile/views_kai.py``).
    The caller has already scoped ``report`` to the user.
    """
    is_submitter = report.submitted_by_id == user.pk
    is_accused = report.targeted_to_id == user.pk

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

    can_appeal, appeal_reason = KaiAppeal.can_file(report, user)

    return {
        'report': report,
        'custom_responses': custom_responses,
        'closure_requests': closure_requests,
        'activity_log': activity_log,
        'can_request_closure': can_request_closure,
        'can_drop_case': can_drop_case,
        'is_submitter': is_submitter,
        'is_accused': is_accused,

        # v3.18.0 — appeals. Bylaws § b.i gives the accused 10 days from the
        # date of notice of a decision. `accused_notified_at` IS that date and
        # was already being recorded; nothing had ever computed the window.
        #
        # The countdown is the point. A ten-day right that nobody is reminded
        # of is a ten-day right nobody uses.
        'can_appeal': can_appeal,
        'appeal_blocked_reason': appeal_reason,
        'appeal_days_remaining': KaiAppeal.days_remaining(report),
        'appeal_window_closes_at': KaiAppeal.window_closes_at(report),
        'appeal_window_days': KaiAppeal.APPEAL_WINDOW_DAYS,
        'appeals': report.appeals.select_related('filed_by').defer(*member_defer('filed_by')),
    }


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

    return render(request, 'kai/user_view_report.html', member_report_context(report, user))


# ---------------------------------------------------------------------------
# A party's requests on their own case: closure, drop, appeal.
#
# v4.4.x — the checks and the saves live here, outside the views, so the
# website (`request_closure`, `request_drop_case`, `file_appeal` below) and the
# app (src/mobile/views_kai_filing.py) refuse the same requests with the same
# words and record, log and email exactly the same things.
# ---------------------------------------------------------------------------

CLOSURE_REASON_REQUIRED = 'Please provide a reason for your closure request.'
CLOSURE_REQUESTED = 'Your closure request has been submitted and is pending review.'
DROP_REASON_REQUIRED = 'Please provide a reason for dropping this case.'
DROP_REQUESTED = 'Your request to drop this case has been submitted and is pending review.'
APPEAL_GROUNDS_REQUIRED = 'Please describe the grounds for your appeal.'


def _short_reason(reason):
    return f'{reason[:100]}...' if len(reason) > 100 else reason


def closure_request_block(report):
    """
    Why a party can't request closure of ``report`` now, as
    ``(code, message level, message)``, or None if they can. The same rule as
    ``member_report_context``'s ``can_request_closure``: the case has been
    addressed, nothing is pending, it isn't archived.
    """
    if report.deliberation_outcome not in ELIGIBLE_OUTCOMES:
        return ('not_addressed', 'error',
                'Closure can only be requested for cases that have been heard or resolved.')
    if report.closure_requests.filter(status='pending').exists():
        return ('request_pending', 'warning', 'A closure request is already pending for this case.')
    if report.status == 'archived':
        return ('archived', 'info', 'This case has already been archived.')
    return None


def drop_request_block(report):
    """
    Why the submitter can't ask to drop ``report`` now, as
    ``(code, message level, message)``, or None. The caller has already scoped
    the case to its submitter (only they may withdraw it).
    """
    # Can't drop if sanctions already applied
    if report.deliberation_outcome == 'sanctions_applied':
        return ('sanctions_applied', 'error',
                'This case cannot be dropped as sanctions have already been applied.')
    if report.closure_requests.filter(status='pending').exists():
        return ('request_pending', 'warning', 'A request is already pending for this case.')
    if report.status == 'archived':
        return ('archived', 'info', 'This case has already been archived.')
    return None


def _kai_chair_emails():
    kai_committee = Committee.objects.get(is_kai_committee=True)
    return [chair.email for chair in kai_committee.chairs.all() if chair.email]


def file_closure_request(report, user, reason, request=None):
    """
    Record ``user``'s (the submitter's or the accused's) request to close
    ``report``: the request, the case activity row, the audit-log row and the
    chairs' email. The caller has checked ``closure_request_block`` and that
    ``reason`` isn't blank.
    """
    is_submitter = report.submitted_by_id == user.pk
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
        details=f'{role} requested case closure. Reason: {_short_reason(reason)}'
    )
    ActivityLog.log_activity(
        action_type='kai_action',
        user=user,
        # v3.18.2 — no name. Only a party (submitter or accused) can
        # request closure, so `user` here is always one of the two
        # people the whole module exists to keep unnamed.
        description=f'A party requested closure of Kai case {report.display_number}',
        request=request,
        object_type='KaiReport',
        object_id=report.id,
        object_repr=report.display_number,
        metadata={'action': 'request_closure'},
    )

    # Notify Kai chair(s) via email
    try:
        chair_emails = _kai_chair_emails()
        if chair_emails:
            send_email.delay(
                f'[Kai] Closure Request: {report.title}',
                f"""A closure request has been submitted for a Kai report.

Report: {report.title}
Requested by: {user.name} ({role})
Request reason: {reason}

Please review this request in the Kai management system.
""",
                settings.DEFAULT_FROM_EMAIL,
                chair_emails,
            )
    except Committee.DoesNotExist:
        pass
    except Exception as e:
        logger.error(f"Failed to queue closure request notification: {e}")

    # v3.25.2 — NEITHER the username NOR the title. This is party-facing, so
    # `user` here is the submitter or the accused, and the line landed
    # verbatim on /officers/system-logs/. The case id names nobody.
    logger.info("Closure requested for Kai report %s", report.pk)
    return closure_request


def file_drop_request(report, user, reason, request=None):
    """
    Record the submitter's request to drop (withdraw) ``report``: the request,
    the case activity row, the audit-log row and the chairs' email. The caller
    has checked ``drop_request_block`` and that ``reason`` isn't blank.
    """
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
        details=f'Submitter requested to drop/withdraw case. Reason: {_short_reason(reason)}'
    )
    ActivityLog.log_activity(
        action_type='kai_action',
        user=user,
        # ⚠️ v3.18.2 — no name, and this one identified the SUBMITTER
        # specifically: only the person who filed a case can withdraw
        # it, so this row was a signed confession of authorship on a
        # page every officer can read.
        description=f'The submitter requested to drop Kai case {report.display_number}',
        request=request,
        object_type='KaiReport',
        object_id=report.id,
        object_repr=report.display_number,
        metadata={'action': 'request_drop'},
    )

    # Notify Kai chair(s) via email
    try:
        chair_emails = _kai_chair_emails()
        if chair_emails:
            send_email.delay(
                f'[Kai] Drop Case Request: {report.title}',
                f"""A request to drop/withdraw a case has been submitted.

Report: {report.title}
Submitted by: {user.name}
Reason for dropping: {reason}

Please review this request in the Kai management system.
""",
                settings.DEFAULT_FROM_EMAIL,
                chair_emails,
            )
    except Committee.DoesNotExist:
        pass
    except Exception as e:
        logger.error(f"Failed to queue drop case request notification: {e}")

    # v3.25.2 — see file_closure_request: party-facing, so the username is a
    # party to the case.
    logger.info("Drop requested for Kai report %s", report.pk)
    return drop_request


def file_kai_appeal(report, user, level, grounds, request=None):
    """
    File the accused's appeal of ``report`` to ``level`` (anything that isn't
    one of ``KaiAppeal.LEVEL_CHOICES`` is the chapter, as the form defaults):
    the appeal, the case activity row and the audit-log row. The caller has
    checked ``KaiAppeal.can_file`` and that ``grounds`` isn't blank.

    No email: the committee sees it on the case timeline.
    """
    if level not in dict(KaiAppeal.LEVEL_CHOICES):
        level = 'chapter'

    appeal = KaiAppeal.objects.create(
        report=report, filed_by=user, level=level, grounds=grounds,
    )

    # The committee needs to see this on the case timeline. `grounds` is the
    # appellant's own words about their own case, so it is not subject to the
    # submitter/accused identity redactions — but it is detail-level content,
    # and the timeline is already gated on can_view_report_details.
    KaiReportActivity.objects.create(
        report=report,
        user=user,
        action='appeal_filed',
        details=f'Appeal filed to {appeal.get_level_display()}.',
    )
    ActivityLog.log_activity(
        action_type='kai_action',
        user=user,
        # ⚠️ v3.18.2 — NO NAME, AND THIS ONE IS THE SHARPEST OF THE FOUR.
        # Only the accused can file an appeal (`KaiAppeal.can_file`, and
        # `file_appeal` fetches with `targeted_to=user`), so ONLY THE ACCUSED
        # CAN EVER WRITE THIS ROW. Naming them here published the accused's
        # identity to every officer and chair; the row's mere existence
        # against a case is the same assertion, which is why `user` is
        # redacted at render rather than this being considered fixed by
        # dropping the name alone. See `src/kai_audit.py`. Do not put a name
        # back in here.
        description=f'An appeal was filed on Kai case {report.display_number}',
        request=request,
        object_type='KaiAppeal',
        metadata={'report_id': report.id, 'level': level},
    )
    return appeal


def appeal_filed_message(appeal):
    return (f'Your appeal to {appeal.get_level_display()} has been filed and the '
            f'committee has been notified.')


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

    # The checks are `closure_request_block`, shared with the app.
    block = closure_request_block(report)
    if block:
        code, level, message = block
        getattr(messages, level)(request, message)
        if code == 'not_addressed':
            return redirect('user_kai_dashboard')
        return redirect('user_view_kai_report', report_id=report_id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()

        if not reason:
            messages.error(request, CLOSURE_REASON_REQUIRED)
        else:
            file_closure_request(report, user, reason, request)
            messages.success(request, CLOSURE_REQUESTED)
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

    # The checks are `drop_request_block`, shared with the app.
    block = drop_request_block(report)
    if block:
        _code, level, message = block
        getattr(messages, level)(request, message)
        return redirect('user_view_kai_report', report_id=report_id)

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()

        if not reason:
            messages.error(request, DROP_REASON_REQUIRED)
        else:
            file_drop_request(report, user, reason, request)
            messages.success(request, DROP_REQUESTED)
            return redirect('user_view_kai_report', report_id=report_id)

    context = {
        'report': report,
    }

    return render(request, 'kai/request_drop.html', context)


@login_required
def user_kai_report_attachment(request, report_id):
    """
    Serve the attachment for a user's own report.

    ⚠️ v3.19.6 — THIS FUNCTION USED TO CHECK ACCESS AND THEN GIVE IT AWAY, and
    it is worth keeping as a worked example rather than deleting quietly.

    The body was: scope the report to `Q(submitted_by=user) | Q(targeted_to=user)`
    — correct, careful, exactly the right predicate — and then
    `return redirect(report.attachment.url)`, i.e. hand the browser
    `/media/kai_reports/<slug>.pdf`. Anyone who wanted the file could request
    that URL directly and never touch this view, so the check protected nothing.
    Its docstring said *"This ensures users can only access their own report
    attachments."* It ensured nothing of the kind.

    **A permission check that ends in a redirect to an unauthenticated URL is
    not a permission check. It is a suggestion.** The redirect target is the
    real access control, and here the redirect target was `/media/`.

    It was also never routed — `urls.py` imports five names from this module and
    this is not one of them — so it was a landmine rather than a live hole: the
    next person to wire it up would have believed the docstring. Kept, delegating
    to the streaming view, so that wiring it up is now safe.
    """
    from src.view.serve_private_upload import serve_kai_report_attachment

    return serve_kai_report_attachment(request, report_id)


@login_required
def file_appeal(request, report_id):
    """
    File an appeal against a Kai decision — bylaws § b.i.

    v3.18.0. The bylaws:

        "Kai Committee decisions can be appealed first to the chapter, then to
         the District Chief, and then to the Board of Trustees and the General
         Convention if needed... all Kai Committee appeals must be made within
         10 days from the date of notice of a decision."

    Every rule about *who* may appeal and *when* lives in `KaiAppeal.can_file`,
    not here, so the view and the template's "Appeal" button cannot disagree —
    the template renders the button from the same call. This is the same
    single-definition shape as `_kai_csv_row`, and for the same reason: two
    copies of a rule is one copy too many.
    """
    user = request.user

    # Only the accused may appeal, so this get() is deliberately narrower than
    # the submitter-or-accused lookup the other member views use.
    report = get_object_or_404(KaiReport, id=report_id, targeted_to=user)

    allowed, reason = KaiAppeal.can_file(report, user)
    if not allowed:
        messages.error(request, reason)
        return redirect('user_view_kai_report', report_id=report.id)

    if request.method != 'POST':
        return redirect('user_view_kai_report', report_id=report.id)

    grounds = (request.POST.get('grounds') or '').strip()
    if not grounds:
        messages.error(request, APPEAL_GROUNDS_REQUIRED)
        return redirect('user_view_kai_report', report_id=report.id)

    # The appeal and its activity rows are `file_kai_appeal`, shared with the app.
    appeal = file_kai_appeal(report, user, request.POST.get('level', 'chapter'), grounds, request)

    messages.success(request, appeal_filed_message(appeal))
    return redirect('user_view_kai_report', report_id=report.id)
