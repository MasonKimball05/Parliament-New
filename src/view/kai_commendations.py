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
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from src.decorators import log_function_call
from src.feature_flag_decorators import require_feature_flag
from src.forms import KaiCommendationForm
from src.models import (
    ActivityLog, Committee, KaiCommendation, KaiCommendationActivity,
    KaiCommendationFieldResponse, KaiFormField, ParliamentUser,
)
from src.utils.file_validation import validate_uploaded_file

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
            commendation = form.save(commit=False)
            commendation.submitted_by = request.user
            commendation.save()

            # Save custom field responses — identical shape to
            # submit_kai_report's handling, pointed at the commendation
            # response table and form_type='commendation' fields.
            custom_fields = _commendation_custom_fields()
            for field in custom_fields:
                field_key = f'custom_field_{field.id}'
                value = request.POST.get(field_key, '').strip()
                file_value = request.FILES.get(field_key)

                if file_value:
                    try:
                        validate_uploaded_file(file_value)
                    except ValidationError as exc:
                        messages.error(request, f'{field.label}: {"; ".join(exc.messages)}')
                        file_value = None

                if value or file_value:
                    response_data = {'commendation': commendation, 'field': field}

                    if field.field_type in ['text', 'textarea', 'email', 'date', 'select', 'radio']:
                        response_data['text_value'] = value
                    elif field.field_type == 'number':
                        try:
                            response_data['number_value'] = float(value) if value else None
                        except ValueError:
                            response_data['text_value'] = value
                    elif field.field_type in ['multiselect', 'checkbox']:
                        values = request.POST.getlist(field_key)
                        response_data['json_value'] = values if values else None
                    elif field.field_type == 'file' and file_value:
                        response_data['file_value'] = file_value
                    elif field.field_type == 'member_select':
                        response_data['text_value'] = value

                    KaiCommendationFieldResponse.objects.create(**response_data)

            KaiCommendationActivity.objects.create(
                commendation=commendation,
                user=request.user,
                action='created',
                details='Commendation submitted',
            )
            # No name in the audit-log description — same reasoning as
            # submit_kai_report's identical omission: request.user IS the
            # submitter here, and django_actions.log is rendered to every
            # officer at /officers/system-logs/. The commended member's
            # name is also left out for the same reason — it's a second
            # named member on a Kai-related log line.
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'A member submitted a Kai commendation {commendation.display_number}',
                request=request,
                object_type='KaiCommendation',
                object_id=commendation.id,
                object_repr=commendation.display_number,
                metadata={'action': 'submitted'},
            )

            _notify_kai_chairs(commendation)

            messages.success(
                request,
                'Your commendation has been submitted. The Kai chair(s) have been notified.',
            )
            return redirect('home')
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
        action = request.POST.get('action')

        if action == 'update_status' and access['can_edit_open_cases']:
            new_status = request.POST.get('status')
            if new_status in dict(KaiCommendation.STATUS_CHOICES):
                old_status = commendation.status
                commendation.status = new_status
                if new_status in ('acknowledged', 'archived') and not commendation.reviewed_at:
                    commendation.mark_reviewed(request.user, new_status)
                else:
                    commendation.save(update_fields=['status'])
                KaiCommendationActivity.objects.create(
                    commendation=commendation, user=request.user,
                    action='status_changed',
                    details=f'Status changed from {old_status} to {new_status}',
                )
                messages.success(request, 'Status updated.')

        elif action == 'assign' and access['can_edit_open_cases']:
            assignee_id = request.POST.get('assigned_to')
            commendation.assigned_to = (
                ParliamentUser.objects.filter(pk=assignee_id).first() if assignee_id else None
            )
            commendation.save(update_fields=['assigned_to'])
            KaiCommendationActivity.objects.create(
                commendation=commendation, user=request.user, action='assigned',
                details=f'Assigned to {commendation.assigned_to.name}' if commendation.assigned_to else 'Unassigned',
            )
            messages.success(request, 'Assignment updated.')

        elif action == 'update_notes' and access['can_add_activity']:
            commendation.committee_notes = request.POST.get('committee_notes', '')
            commendation.save(update_fields=['committee_notes'])
            KaiCommendationActivity.objects.create(
                commendation=commendation, user=request.user, action='notes_updated',
                details='Committee notes updated',
            )
            messages.success(request, 'Notes updated.')

        else:
            messages.error(request, 'You do not have permission to make this change.')

        return redirect('manage_kai_commendation_detail', commendation_id=commendation.id)

    custom_responses = commendation.custom_responses.select_related('field').all()
    committee_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'kai/commendation_detail.html', {
        'commendation': commendation,
        'custom_responses': custom_responses,
        'activity_log': commendation.activity_log.select_related('user').all(),
        'committee_members': committee_members,
        'access': access,
    })
