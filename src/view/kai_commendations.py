"""
Kai commendations — v3.28.9 (corrects v3.28.8's "accommodation" wording
mistake). See src/models/kai_commendations.py::KaiCommendation's docstring
for why this is a separate model (and separate view module) from KaiReport.

Member view:
  - submit_kai_commendation      — submit a commendation for another member

Committee views (gated by the SAME KaiMemberPermission grants as
disciplinary reports — see _get_kai_access, imported from kai_reports.py
rather than reimplemented, so the two forms' access control can never
drift apart):
  - manage_kai_commendations         — list
  - manage_kai_commendation_detail   — view / update one commendation
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from src.decorators import log_function_call
from src.feature_flag_decorators import require_feature_flag
from src.forms import KaiCommendationForm
from src.models import (
    ActivityLog, Committee, KaiCommendation, KaiCommendationActivity,
    KaiCommendationFieldResponse, KaiFormField, ParliamentUser,
)
from src.utils.file_validation import validate_uploaded_file
from src.utils.form_messages import queue_form_errors
from src.view.kai_reports import save_kai_custom_responses

logger = logging.getLogger('src')


# ---------------------------------------------------------------------------
# Member: Submit
# ---------------------------------------------------------------------------

@login_required
@require_feature_flag('kai_reports')
@log_function_call
def submit_kai_commendation(request):
    """Allow any logged-in user to commend another member to the Kai
    Committee. Mirrors submit_kai_report's shape (kai_reports.py) — same
    custom-field handling, same file-validation-before-anything-is-saved
    discipline — for a form with a much smaller builtin field set, plus one
    required field neither KaiReport nor the old (misnamed) accommodation
    form had: `commended_member`, the whole point of this form."""
    if request.method == 'POST':
        if 'attachment' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['attachment'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                form = KaiCommendationForm()
                return render(request, 'kai/submit_commendation.html', {
                    'form': form,
                    'custom_fields': _commendation_custom_fields(),
                })

        form = KaiCommendationForm(request.POST, request.FILES)
        if form.is_valid():
            # v4.4.x — `file_kai_commendation`, shared with the app
            # (src/mobile/views_kai_filing.py).
            _commendation, notes = file_kai_commendation(
                request.user, form, request.POST, request.FILES, request)
            for note in notes:
                messages.error(request, note)

            messages.success(
                request,
                'Your commendation has been submitted. The Kai chair(s) have been notified.',
            )
            return redirect('home')
        queue_form_errors(request, form)
    else:
        form = KaiCommendationForm()

    custom_fields = _commendation_custom_fields()
    custom_sections = {}
    for field in custom_fields:
        section = field.section or 'Additional Information'
        custom_sections.setdefault(section, []).append(field)

    return render(request, 'kai/submit_commendation.html', {
        'form': form,
        'custom_fields': custom_fields,
        'custom_sections': custom_sections,
    })


def _commendation_custom_fields():
    return KaiFormField.objects.filter(
        is_active=True, is_builtin=False, form_type='commendation',
    ).order_by('section', 'display_order')


def file_kai_commendation(user, form, data, files, request=None):
    """
    File a valid ``KaiCommendationForm`` as ``user``'s commendation: the
    commendation, its custom answers (``save_kai_custom_responses``, the
    report form's own saver, pointed at the commendation response table and
    form_type='commendation' fields), the activity rows and the chairs' email.
    Returns ``(commendation, notes)``; ``notes`` are refused custom-field
    files.

    The one save path, used by ``submit_kai_commendation`` and the app
    (src/mobile/views_kai_filing.py).
    """
    commendation = form.save(commit=False)
    commendation.submitted_by = user
    commendation.save()

    notes = save_kai_custom_responses(
        _commendation_custom_fields(), data, files,
        lambda **answer: KaiCommendationFieldResponse.objects.create(commendation=commendation, **answer))

    KaiCommendationActivity.objects.create(
        commendation=commendation,
        user=user,
        action='created',
        details='Commendation submitted',
    )
    # No name in the audit-log description — same reasoning as
    # file_kai_report's identical omission: `user` IS the submitter here, and
    # django_actions.log is rendered to every officer at
    # /officers/system-logs/. The commended member's name is also left out
    # for the same reason — it's a second named member on a Kai-related log
    # line.
    ActivityLog.log_activity(
        action_type='kai_action',
        user=user,
        description=f'A member submitted a Kai commendation {commendation.display_number}',
        request=request,
        object_type='KaiCommendation',
        object_id=commendation.id,
        object_repr=commendation.display_number,
        metadata={'action': 'submitted'},
    )

    _notify_kai_chairs(commendation)
    return commendation, notes


def _notify_kai_chairs(commendation):
    """Email the Kai chair(s) only — mirrors submit_kai_report's own
    chair-only notification exactly (never the whole committee)."""
    from django.conf import settings

    from src.tasks import send_email

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        kai_chairs = kai_committee.chairs.all()
        recipient_emails = [c.email for c in kai_chairs if c.email]

        if recipient_emails:
            subject = f'New Kai Commendation: {commendation.title}'
            message = f"""
A new Kai commendation has been submitted.

Summary: {commendation.title}
Commending: {commendation.commended_member.name}

Details:
{commendation.description}

Please log in to the Kai Committee page to review this commendation.
            """
            send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)
            logger.info(
                "[KAI EMAIL] Email queued for Kai commendation %s",
                commendation.pk,
            )
        else:
            logger.warning('[KAI EMAIL] No recipient emails found for Kai commendation notification')
    except Committee.DoesNotExist:
        logger.warning('[KAI EMAIL] KAI committee not found - cannot send commendation notification')
    except Exception as e:
        logger.error(f'[KAI EMAIL] Failed to send Kai commendation email: {e}')


# ---------------------------------------------------------------------------
# Committee: Manage
# ---------------------------------------------------------------------------

#: Same bound as KAI_LIST_LIMIT in kai_reports.py, same reasoning — the list
#: was unbounded before either module existed; keep both bounded rather than
#: importing one constant into a shape that could later diverge.
KAI_COMMENDATION_LIST_LIMIT = 500


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_commendations(request):
    """Committee list view, gated by the shared KaiMemberPermission grants."""
    from src.view.kai_reports import _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    access = _get_kai_access(request.user, kai_committee)
    if not access['can_view_report_list']:
        messages.error(request, 'You do not have permission to view Kai commendations.')
        return redirect('home')

    status_filter = request.GET.get('status', '')
    commendations_list, truncated = commendation_list(status_filter)

    return render(request, 'kai/manage_commendations.html', {
        'commendations': commendations_list,
        'commendations_truncated': truncated,
        'status_choices': KaiCommendation.STATUS_CHOICES,
        'status_filter': status_filter,
        'access': access,
    })


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_commendation_detail(request, commendation_id):
    """View / update a single commendation."""
    from src.view.kai_reports import _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    access = _get_kai_access(request.user, kai_committee)
    if not access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this commendation.')
        return redirect('home')

    commendation = get_object_or_404(KaiCommendation, id=commendation_id)

    if request.method == 'POST':
        try:
            done = update_commendation(commendation, request.user, access,
                                       request.POST.get('action'), request.POST)
        except PermissionDenied:
            messages.error(request, 'You do not have permission to make this change.')
        else:
            if done:
                messages.success(request, done)

        return redirect('manage_kai_commendation_detail', commendation_id=commendation.id)

    custom_responses = commendation.custom_responses.select_related('field').all()
    committee_members = assignable_members()

    return render(request, 'kai/commendation_detail.html', {
        'commendation': commendation,
        'custom_responses': custom_responses,
        'activity_log': commendation.activity_log.select_related('user').all(),
        'committee_members': committee_members,
        'access': access,
    })


# ---------------------------------------------------------------------------
# Shared with the app (src/mobile/views_kai_commendations.py)
# ---------------------------------------------------------------------------

def commendation_list(status_filter=''):
    """The list page's rows (newest first, at most KAI_COMMENDATION_LIST_LIMIT)
    and whether more matched. ``status_filter`` '' means every status."""
    from src.models.users import member_defer

    # `submitted_by` and `commended_member` are always select_related — both
    # are needed either way to render the list row, and this only controls
    # whether the NAME fields on them are fetched. Same shape as
    # kai_reports.py's own list view.
    commendations_qs = (
        KaiCommendation.objects
        .select_related('assigned_to', 'reviewed_by', 'submitted_by', 'commended_member')
        .defer(*member_defer('assigned_to', 'reviewed_by', 'submitted_by', 'commended_member'))
    )

    if status_filter:
        commendations_qs = commendations_qs.filter(status=status_filter)

    commendations_list = list(commendations_qs[:KAI_COMMENDATION_LIST_LIMIT])
    truncated = commendations_qs.count() > KAI_COMMENDATION_LIST_LIMIT
    return commendations_list, truncated


def assignable_members():
    """Who the detail page's Assignment picker offers: every active member."""
    return ParliamentUser.objects.filter(member_status='Active').order_by('name')


def update_commendation(commendation, user, access, action, data):
    """
    The detail page's three forms: ``update_status`` (``status``) and
    ``assign`` (``assigned_to``, a member's pk; empty unassigns) need
    can_edit_open_cases, ``update_notes`` (``committee_notes``) needs
    can_add_activity. Each writes a KaiCommendationActivity row.

    Returns the success message, or '' when ``update_status`` named a status
    that isn't one (the page then changes nothing and says nothing). Raises
    PermissionDenied for an action this access doesn't allow, or an unknown
    action — the page's "You do not have permission to make this change."
    """
    if action == 'update_status' and access['can_edit_open_cases']:
        new_status = data.get('status')
        if new_status not in dict(KaiCommendation.STATUS_CHOICES):
            return ''
        old_status = commendation.status
        commendation.status = new_status
        if new_status in ('acknowledged', 'archived') and not commendation.reviewed_at:
            commendation.mark_reviewed(user, new_status)
        else:
            commendation.save(update_fields=['status'])
        KaiCommendationActivity.objects.create(
            commendation=commendation, user=user,
            action='status_changed',
            details=f'Status changed from {old_status} to {new_status}',
        )
        return 'Status updated.'

    if action == 'assign' and access['can_edit_open_cases']:
        assignee_id = data.get('assigned_to')
        commendation.assigned_to = (
            ParliamentUser.objects.filter(pk=assignee_id).first() if assignee_id else None
        )
        commendation.save(update_fields=['assigned_to'])
        KaiCommendationActivity.objects.create(
            commendation=commendation, user=user, action='assigned',
            details=f'Assigned to {commendation.assigned_to.name}' if commendation.assigned_to else 'Unassigned',
        )
        return 'Assignment updated.'

    if action == 'update_notes' and access['can_add_activity']:
        commendation.committee_notes = data.get('committee_notes', '')
        commendation.save(update_fields=['committee_notes'])
        KaiCommendationActivity.objects.create(
            commendation=commendation, user=user, action='notes_updated',
            details='Committee notes updated',
        )
        return 'Notes updated.'

    raise PermissionDenied('You do not have permission to make this change.')
