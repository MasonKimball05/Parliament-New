"""
Logging service hours in the app (v4.4.0): the website's Submit Service Hours
form (/service-hours/submit/) and its Edit page (/service-hours/edit/<id>/),
saved by the same functions the web views use (``create_service_submissions``
and ``update_service_submission`` in src/view/service_user_dashboard.py), so
the checks (``ServiceHoursSubmissionForm``), the extra-date rows, the
officer-defined custom fields, the activity log and the VPP email are the
website's.

* ``GET service-hours/form/`` — the form: the active periods (and which one the
  dashboard calls current), the custom fields from the form builder (each with
  the name it is posted under, ``custom_<field_name>``), the attachment limits
  and how many extra dates one submission can carry.
* ``POST service-hours/submissions/`` — a submission, as ``multipart/form-data``
  (an ``attachment`` and any ``custom_*`` file fields alongside the fields) or
  JSON. Fields: ``period``, ``hours``, ``service_date`` (YYYY-MM-DD),
  ``organization``, ``description``, ``extra_dates[]``/``extra_hours[]``
  (index-aligned) and ``custom_<name>`` (a list for checkbox / multi-select).
  201 ``{ids, status, message, notes}``; ``notes`` are what the website shows
  as warnings (a skipped extra date, a refused custom-field file). 400
  ``invalid_input`` with the form's errors; 400 ``no_period`` when no period is
  open.
* ``GET service-hours/submissions/<id>/`` — one of the member's own
  submissions, with its custom-field answers and whether it can still be
  edited. ``POST`` — the edited submission, the same fields as creating one
  (no extra dates, like the website's edit page); a rejected one goes back for
  approval. 400 ``not_editable`` once it's approved.

Every signed-in member, like the web pages (``@login_required`` only, no page
toggle), with the pledge page restrictions of each. The website has no
"withdraw", so neither does this.
"""
import os

from django.http import QueryDict
from django.shortcuts import get_object_or_404
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.forms import ServiceHoursSubmissionForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import ServiceFieldResponse, ServiceHoursSubmission, ServicePeriod
from src.view.service_user_dashboard import (
    MAX_EXTRA_SERVICE_DATES, SERVICE_NOT_EDITABLE, create_service_submissions, current_service_period,
    service_custom_fields, service_submitted_message, service_updated_message, update_service_submission,
)

#: ServiceHoursSubmissionForm.clean_attachment's limits.
ATTACHMENT_EXTENSIONS = ['.pdf', '.jpg', '.jpeg', '.png', '.docx']
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024

#: The field types the app draws natively; anything else is sent to the website.
NATIVE_TYPES = {'text', 'textarea', 'number', 'date', 'select', 'radio', 'checkbox', 'multiselect', 'file'}


def _num(d):
    return float(d) if d is not None else None


def _form_data(request):
    """The request's fields as the QueryDict the web helpers read.

    Multipart is ``request.POST`` (the files are ``request.FILES``); JSON is
    turned into one, a list becoming repeated values (``getlist``)."""
    if isinstance(request.data, QueryDict):
        return request.POST
    data = QueryDict(mutable=True)
    if hasattr(request.data, 'items'):
        for key, value in request.data.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                data.setlist(key, [str(v) for v in value if v is not None])
            elif isinstance(value, bool):
                data[key] = 'on' if value else ''
            else:
                data[key] = str(value)
    return data


def _form_errors(form):
    """``queue_form_errors``' messages, joined into one line for the app."""
    lines = []
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else None
        lines.extend(f'{label}: {e}' if label else str(e) for e in errors)
    return ' '.join(lines) or 'Check the form and try again.'


def _field_json(field):
    return {
        'name': f'custom_{field.field_name}',
        'label': field.label,
        'type': field.field_type if field.field_type in NATIVE_TYPES else 'unsupported',
        'placeholder': field.placeholder or '',
        'help_text': field.help_text or '',
        'options': [str(o) for o in (field.options or [])],
        'required': field.is_required,
        'section': field.section or '',
    }


def _response_value(response):
    """A saved answer, in the shape the form posts it back."""
    kind = response.field.field_type
    if kind in ('multiselect', 'checkbox'):
        return [str(v) for v in (response.json_value or [])]
    if kind == 'file':
        return os.path.basename(response.file_value.name) if response.file_value else None
    if kind == 'number' and response.number_value is not None:
        return format(response.number_value.normalize(), 'f')
    return response.text_value or ''


class ServiceHoursFormView(MobileAPIView):
    web_page = 'submit_service_hours'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        current = current_service_period()
        periods = ServicePeriod.objects.filter(is_active=True)   # the form's queryset
        return Response({
            'periods': [{
                'id': p.id,
                'name': p.name,
                'start_date': p.start_date.isoformat(),
                'end_date': p.end_date.isoformat(),
                'requires_approval': p.requires_approval,
            } for p in periods],
            'current_period_id': current.id if current else None,
            'fields': [_field_json(f) for f in service_custom_fields()],
            'attachment': {'extensions': ATTACHMENT_EXTENSIONS, 'max_bytes': ATTACHMENT_MAX_BYTES},
            'max_extra_dates': MAX_EXTRA_SERVICE_DATES,
        })


class ServiceSubmissionsView(MobileAPIView):
    web_page = 'submit_service_hours'
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request):
        if not ServicePeriod.objects.filter(is_active=True).exists():
            raise BadRequest('No active service periods available. Please contact the VPP.', code='no_period')
        data = _form_data(request)
        form = ServiceHoursSubmissionForm(data, request.FILES)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        notes = []
        rows = create_service_submissions(request.user, form, data, request.FILES, service_custom_fields(), notes)
        return Response({
            'ids': [s.id for s in rows],
            'status': rows[0].status,
            'message': service_submitted_message(rows),
            'notes': [text for _, text in notes],
        }, status=201)


class ServiceSubmissionDetailView(MobileAPIView):
    web_page = 'edit_service_submission'
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def _submission(self, request, submission_id):
        return get_object_or_404(ServiceHoursSubmission, submitted_by=request.user, id=submission_id)

    def get(self, request, submission_id):
        s = self._submission(request, submission_id)
        responses = (ServiceFieldResponse.objects.filter(submission=s)
                     .select_related('field').order_by('field__display_order'))
        return Response({
            'id': s.id,
            'period_id': s.period_id,
            'period': s.period.name,
            'hours': _num(s.hours),
            'service_date': s.service_date.isoformat(),
            'organization': s.organization,
            'description': s.description,
            'status': s.status,
            'reviewer_notes': s.reviewer_notes or '',
            'attachment_name': os.path.basename(s.attachment.name) if s.attachment else None,
            'can_edit': s.can_edit(),
            'responses': {f'custom_{r.field.field_name}': _response_value(r) for r in responses},
        })

    def post(self, request, submission_id):
        s = self._submission(request, submission_id)
        if not s.can_edit():
            raise BadRequest(SERVICE_NOT_EDITABLE, code='not_editable')
        was_rejected = s.status == 'rejected'
        data = _form_data(request)
        form = ServiceHoursSubmissionForm(data, request.FILES, instance=s)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        notes = []
        s = update_service_submission(
            request.user, form, data, request.FILES, service_custom_fields(), was_rejected, notes)
        return Response({
            'id': s.id,
            'status': s.status,
            'message': service_updated_message(was_rejected),
            'notes': [text for _, text in notes],
        })
