"""
Kai accommodation requests — v3.28.8. See
src/models/kai_accommodations.py::KaiAccommodationRequest's docstring for
why this is a separate model (and separate view module) from KaiReport
rather than a flag on it.

Member view:
  - submit_kai_accommodation_request   — submit a request

Committee views (gated by the SAME KaiMemberPermission grants as
disciplinary reports — see _get_kai_access, imported from kai_reports.py
rather than reimplemented, so the two forms' access control can never
drift apart):
  - manage_kai_accommodation_requests        — list
  - manage_kai_accommodation_request_detail  — view / update one request
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from src.decorators import log_function_call
from src.feature_flag_decorators import require_feature_flag
from src.forms import KaiAccommodationRequestForm
from src.models import (
    ActivityLog, Committee, KaiAccommodationFieldResponse,
    KaiAccommodationRequest, KaiAccommodationRequestActivity, KaiFormField,
    ParliamentUser,
)
from src.utils.file_validation import validate_uploaded_file

logger = logging.getLogger('src')


# ---------------------------------------------------------------------------
# Member: Submit
# ---------------------------------------------------------------------------

@login_required
@require_feature_flag('kai_reports')
@log_function_call
def submit_kai_accommodation_request(request):
    """Allow any logged-in user to request an accommodation from the Kai
    Committee. Mirrors submit_kai_report's shape (kai_reports.py) — same
    custom-field handling, same file-validation-before-anything-is-saved
    discipline — for a form with a much smaller builtin field set."""
    if request.method == 'POST':
        if 'attachment' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['attachment'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                form = KaiAccommodationRequestForm()
                return render(request, 'kai/submit_accommodation_request.html', {
                    'form': form,
                    'custom_fields': _accommodation_custom_fields(),
                })

        form = KaiAccommodationRequestForm(request.POST, request.FILES)
        if form.is_valid():
            accommodation_request = form.save(commit=False)
            accommodation_request.requester = request.user
            accommodation_request.save()

            # Save custom field responses — identical shape to
            # submit_kai_report's handling, pointed at the accommodation
            # response table and form_type='accommodation' fields.
            custom_fields = _accommodation_custom_fields()
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
                    response_data = {'request': accommodation_request, 'field': field}

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

                    KaiAccommodationFieldResponse.objects.create(**response_data)

            KaiAccommodationRequestActivity.objects.create(
                request=accommodation_request,
                user=request.user,
                action='created',
                details='Accommodation request created',
            )
            # No name in the audit-log description — same reasoning as
            # submit_kai_report's identical omission: request.user IS the
            # requester here, and django_actions.log is rendered to every
            # officer at /officers/system-logs/.
            ActivityLog.log_activity(
                action_type='kai_action',
                user=request.user,
                description=f'A member submitted Kai accommodation request {accommodation_request.display_number}',
                request=request,
                object_type='KaiAccommodationRequest',
                object_id=accommodation_request.id,
                object_repr=accommodation_request.display_number,
                metadata={'action': 'submitted'},
            )

            _notify_kai_chairs(accommodation_request)

            messages.success(
                request,
                'Your accommodation request has been submitted. The Kai chair(s) have been notified.',
            )
            return redirect('home')
    else:
        form = KaiAccommodationRequestForm()

    custom_fields = _accommodation_custom_fields()
    custom_sections = {}
    for field in custom_fields:
        section = field.section or 'Additional Information'
        custom_sections.setdefault(section, []).append(field)

    return render(request, 'kai/submit_accommodation_request.html', {
        'form': form,
        'custom_fields': custom_fields,
        'custom_sections': custom_sections,
    })


def _accommodation_custom_fields():
    return KaiFormField.objects.filter(
        is_active=True, is_builtin=False, form_type='accommodation',
    ).order_by('section', 'display_order')


def _notify_kai_chairs(accommodation_request):
    """Email the Kai chair(s) only — mirrors submit_kai_report's own
    chair-only notification exactly (never the whole committee, never the
    requester's identity beyond what the chairs already have permission
    to see by being chairs)."""
    from django.conf import settings

    from src.tasks import send_email

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
        kai_chairs = kai_committee.chairs.all()
        recipient_emails = [c.email for c in kai_chairs if c.email]

        if recipient_emails:
            subject = f'New Kai Accommodation Request: {accommodation_request.title}'
            message = f"""
A new Kai accommodation request has been submitted.

Summary: {accommodation_request.title}
Submitted by: {accommodation_request.requester.name}

Details:
{accommodation_request.description}

Please log in to the Kai Committee page to review this request.
            """
            send_email.delay(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_emails)
            logger.info(
                "[KAI EMAIL] Email queued for Kai accommodation request %s",
                accommodation_request.pk,
            )
        else:
            logger.warning('[KAI EMAIL] No recipient emails found for Kai accommodation request notification')
    except Committee.DoesNotExist:
        logger.warning('[KAI EMAIL] KAI committee not found - cannot send accommodation notification')
    except Exception as e:
        logger.error(f'[KAI EMAIL] Failed to send Kai accommodation request email: {e}')


# ---------------------------------------------------------------------------
# Committee: Manage
# ---------------------------------------------------------------------------

#: Same bound as KAI_LIST_LIMIT in kai_reports.py, same reasoning — the list
#: was unbounded before either module existed; keep both bounded rather than
#: importing one constant into a shape that could later diverge.
KAI_ACCOMMODATION_LIST_LIMIT = 500


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_accommodation_requests(request):
    """Committee list view, gated by the shared KaiMemberPermission grants."""
    from src.view.kai_reports import _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    access = _get_kai_access(request.user, kai_committee)
    if not access['can_view_report_list']:
        messages.error(request, 'You do not have permission to view Kai accommodation requests.')
        return redirect('home')

    status_filter = request.GET.get('status', '')
    from src.models.users import member_defer

    # `requester` is always select_related (the row is needed either way to
    # decide per-request whether to show or redact the name — see the
    # template) — this only controls whether the NAME fields on it are
    # fetched. Same shape as kai_reports.py's own list view.
    requests_qs = (
        KaiAccommodationRequest.objects
        .select_related('assigned_to', 'resolved_by', 'requester')
        .defer(*member_defer('assigned_to', 'resolved_by', 'requester'))
    )

    if status_filter:
        requests_qs = requests_qs.filter(status=status_filter)

    requests_list = list(requests_qs[:KAI_ACCOMMODATION_LIST_LIMIT])
    truncated = requests_qs.count() > KAI_ACCOMMODATION_LIST_LIMIT

    return render(request, 'kai/manage_accommodation_requests.html', {
        'requests': requests_list,
        'requests_truncated': truncated,
        'status_choices': KaiAccommodationRequest.STATUS_CHOICES,
        'status_filter': status_filter,
        'access': access,
    })


@login_required
@require_feature_flag('kai_reports')
@log_function_call
def manage_kai_accommodation_request_detail(request, request_id):
    """View / update a single accommodation request."""
    from src.view.kai_reports import _get_kai_access

    try:
        kai_committee = Committee.objects.get(is_kai_committee=True)
    except Committee.DoesNotExist:
        messages.error(request, 'Kai committee not found.')
        return redirect('home')

    access = _get_kai_access(request.user, kai_committee)
    if not access['can_view_report_details']:
        messages.error(request, 'You do not have permission to view this request.')
        return redirect('home')

    accommodation_request = get_object_or_404(KaiAccommodationRequest, id=request_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_status' and access['can_edit_open_cases']:
            new_status = request.POST.get('status')
            if new_status in dict(KaiAccommodationRequest.STATUS_CHOICES):
                old_status = accommodation_request.status
                accommodation_request.status = new_status
                if new_status in ('approved', 'denied', 'closed') and not accommodation_request.resolved_at:
                    accommodation_request.mark_resolved(request.user, new_status)
                else:
                    accommodation_request.save(update_fields=['status'])
                KaiAccommodationRequestActivity.objects.create(
                    request=accommodation_request, user=request.user,
                    action='status_changed',
                    details=f'Status changed from {old_status} to {new_status}',
                )
                messages.success(request, 'Status updated.')

        elif action == 'assign' and access['can_edit_open_cases']:
            assignee_id = request.POST.get('assigned_to')
            accommodation_request.assigned_to = (
                ParliamentUser.objects.filter(pk=assignee_id).first() if assignee_id else None
            )
            accommodation_request.save(update_fields=['assigned_to'])
            KaiAccommodationRequestActivity.objects.create(
                request=accommodation_request, user=request.user, action='assigned',
                details=f'Assigned to {accommodation_request.assigned_to.name}' if accommodation_request.assigned_to else 'Unassigned',
            )
            messages.success(request, 'Assignment updated.')

        elif action == 'update_notes' and access['can_add_activity']:
            accommodation_request.committee_notes = request.POST.get('committee_notes', '')
            accommodation_request.save(update_fields=['committee_notes'])
            KaiAccommodationRequestActivity.objects.create(
                request=accommodation_request, user=request.user, action='notes_updated',
                details='Committee notes updated',
            )
            messages.success(request, 'Notes updated.')

        else:
            messages.error(request, 'You do not have permission to make this change.')

        return redirect('manage_kai_accommodation_request_detail', request_id=accommodation_request.id)

    custom_responses = accommodation_request.custom_responses.select_related('field').all()
    committee_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'kai/accommodation_request_detail.html', {
        'accommodation_request': accommodation_request,
        'custom_responses': custom_responses,
        'activity_log': accommodation_request.activity_log.select_related('user').all(),
        'committee_members': committee_members,
        'access': access,
    })
