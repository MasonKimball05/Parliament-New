"""
Filing with Kai from the app (v4.4.x): the member side of the website's Kai
forms, saved and checked by the same helpers the web views call, so what is
recorded, who is emailed and what stays confidential can't drift between the
two.

* ``GET kai/form/`` — the Submit Kai Report form (``submit_kai_report``,
  /kai/submit-report/): categories, the tag vocabulary, the active members
  (the "Directed to" picker and any member-selection field), the active
  templates, the form builder's discipline fields (each with the name it is
  posted under, ``custom_field_<id>``) and the attachment limits.
* ``POST kai/reports/`` — a report, as ``multipart/form-data`` (an
  ``attachment`` and any ``custom_field_<id>`` photo alongside the fields) or
  JSON: ``category``, ``title``, ``description``, ``targeted_to`` (a member id,
  or empty), ``tags`` (comma-separated, from the vocabulary) and the custom
  answers (a list for checkbox / multi-select). Saved by ``file_kai_report``
  (src/view/kai_reports.py). 201 ``{id, message, notes}``; ``notes`` are the
  custom-field files the website would also refuse after saving.
* ``GET kai/commendations/form/`` / ``POST kai/commendations/`` — the Submit
  Commendation form (``submit_kai_commendation``): ``commended_member``,
  ``title``, ``description``, ``attachment``, ``is_submitter_anonymous`` and the
  commendation fields. Saved by ``file_kai_commendation``. 201
  ``{id, message, notes}``.
* ``POST kai/reports/<id>/closure/`` ``{reason}`` — the submitter or the
  accused asks for closure (``request_closure``); ``closure_request_block``
  and ``file_closure_request`` in src/view/kai_user_dashboard.py.
* ``POST kai/reports/<id>/drop/`` ``{reason}`` — the submitter asks to drop
  their case (``request_drop_case``); ``drop_request_block`` /
  ``file_drop_request``.
* ``POST kai/reports/<id>/appeal/`` ``{level, grounds}`` — the accused appeals
  (``file_appeal``); every who-and-when rule is ``KaiAppeal.can_file``, the
  save is ``file_kai_appeal``.

The three case actions answer 201 ``{message}``; a refusal is 400 with the
website's own words and a code: ``not_addressed``, ``request_pending``,
``archived``, ``sanctions_applied``, ``appeal_unavailable``, or
``invalid_input`` for a blank reason. A case the member isn't a party to is
404, and so is a case the accused can't see yet (outcome ``pending``) — the
same tightening as ``views_kai.KaiReportView``, so the app never confirms a
case exists before the accused is meant to know about it.

Nothing here returns who filed a case. The accused's endpoints answer with
their own request only.

The two submit forms carry ``@require_feature_flag('kai_reports')`` on the
website, so they 403 ``feature_disabled`` here when it's off; the case pages
and the three request views have only ``@login_required``, so the case actions
don't (as there). None of the pages has ``@require_page_enabled``; the pledge
restrictions of each apply.
"""
from django.db.models import Q
from django.http import Http404
from rest_framework import exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.forms import KaiCommendationForm, KaiReportForm
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data, _form_errors
from src.models import KaiAppeal, KaiReport, KaiReportTemplate, ParliamentUser
from src.utils.file_validation import MAX_FILE_SIZE
from src.view.kai_commendations import _commendation_custom_fields, file_kai_commendation
from src.view.kai_reports import file_kai_report, kai_report_custom_fields
from src.view.kai_user_dashboard import (
    APPEAL_GROUNDS_REQUIRED, CLOSURE_REASON_REQUIRED, CLOSURE_REQUESTED, DROP_REASON_REQUIRED, DROP_REQUESTED,
    appeal_filed_message, closure_request_block, drop_request_block, file_closure_request, file_drop_request,
    file_kai_appeal,
)

#: KaiReportForm.clean_attachment's limits. v4.4.2: its whole list (build 19's
#: file picker sends PDFs and Office files; older builds send JPG or PNG).
REPORT_ATTACHMENT = {'extensions': ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.jpg', '.jpeg', '.png'],
                     'max_bytes': 20 * 1024 * 1024}

#: The website's success messages (messages.success in the two submit views).
REPORT_SUBMITTED = 'Your Kai report has been submitted successfully! The Kai chair(s) have been notified.'
COMMENDATION_SUBMITTED = 'Your commendation has been submitted. The Kai chair(s) have been notified.'

#: The form builder's types; the app draws them all (a file field takes a photo,
#: or with build 19's file picker a document).
FIELD_TYPES = {'text', 'textarea', 'number', 'email', 'date', 'select', 'multiselect', 'checkbox', 'radio',
               'file', 'member_select'}


def _require_kai():
    if not check_feature_enabled('kai_reports'):
        raise exceptions.PermissionDenied('Kai reports are off.', code='feature_disabled')


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _option(o):
    """A form-builder option as the template renders it: ``option.value|default:option``."""
    if isinstance(o, dict):
        value = o.get('value') or ''
        return {'value': str(value), 'label': str(o.get('label') or value)}
    return {'value': str(o), 'label': str(o)}


def _field_json(field):
    return {
        'name': f'custom_field_{field.id}',
        'label': field.label,
        'type': field.field_type if field.field_type in FIELD_TYPES else 'unsupported',
        'placeholder': field.placeholder or '',
        'help_text': field.help_text or '',
        'options': [_option(o) for o in (field.options or [])],
        'required': field.is_required,
        # The website's regroup: no section is "Additional Information".
        'section': field.section or 'Additional Information',
    }


def _active_members():
    """The pickers' list: the website's ``member_status='Active'`` queryset, by name."""
    return [{'id': m.pk, 'name': m.name}
            for m in ParliamentUser.objects.filter(member_status='Active').order_by('name').only('pk', 'name')]


def party_case(user, report_id, **scope):
    """
    ``report_id`` as one of its parties may act on it, or 404: the website's
    ``submitted_by=user | targeted_to=user`` lookup (narrowed by ``scope``), and
    nothing for the accused while the outcome is ``pending`` (see
    ``views_kai``'s docstring).
    """
    qs = KaiReport.objects.filter(id=report_id)
    qs = qs.filter(**scope) if scope else qs.filter(Q(submitted_by=user) | Q(targeted_to=user))
    report = qs.first()
    if report is None:
        raise Http404
    if report.submitted_by_id != user.pk and report.deliberation_outcome == 'pending':
        raise Http404
    return report


def _reason(request, key, message):
    text = request.data.get(key) if hasattr(request.data, 'get') else None
    text = (text if isinstance(text, str) else '').strip()
    if not text:
        raise BadRequest(message, code='invalid_input')
    return text


# ── Submit a report ──────────────────────────────────────────────────────────

class KaiReportFormView(MobileAPIView):
    web_page = 'submit_kai_report'
    web_page_toggle = False   # @login_required + @require_feature_flag only

    def get(self, request):
        _require_kai()
        templates = [{
            'id': t.id,
            'name': t.name,
            'category': t.category,
            'category_label': t.get_category_display(),
            'title': t.title_template,
            'description': t.description_template,
            # The vocabulary's values; the form only accepts those.
            'tags': KaiReport.normalize_tags(t.suggested_tags)[0],
        } for t in KaiReportTemplate.objects.filter(is_active=True)]
        return Response({
            'categories': _choices(KaiReport.CATEGORY_CHOICES),
            'tags': _choices(KaiReport.TAG_CHOICES),
            'members': _active_members(),
            'templates': templates,
            'fields': [_field_json(f) for f in kai_report_custom_fields()],
            'attachment': REPORT_ATTACHMENT,
        })


class KaiReportSubmitView(MobileAPIView):
    web_page = 'submit_kai_report'
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request):
        _require_kai()
        data = _form_data(request)
        form = KaiReportForm(data, request.FILES)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        report, notes = file_kai_report(request.user, form, data, request.FILES, request._request)
        return Response({'id': report.id, 'message': REPORT_SUBMITTED, 'notes': notes}, status=201)


# ── Submit a commendation ────────────────────────────────────────────────────

class KaiCommendationFormView(MobileAPIView):
    web_page = 'submit_kai_commendation'
    web_page_toggle = False   # @login_required + @require_feature_flag only

    def get(self, request):
        _require_kai()
        return Response({
            'members': _active_members(),
            'fields': [_field_json(f) for f in _commendation_custom_fields()],
            # validate_uploaded_file's limit (KaiCommendationForm.clean_attachment).
            'attachment': {'max_bytes': MAX_FILE_SIZE},
        })


class KaiCommendationSubmitView(MobileAPIView):
    web_page = 'submit_kai_commendation'
    web_page_toggle = False
    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request):
        _require_kai()
        data = _form_data(request)
        form = KaiCommendationForm(data, request.FILES)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        commendation, notes = file_kai_commendation(request.user, form, data, request.FILES, request._request)
        return Response({'id': commendation.id, 'message': COMMENDATION_SUBMITTED, 'notes': notes}, status=201)


# ── A party's requests on their own case ─────────────────────────────────────

def _refuse(block):
    code, _level, message = block
    raise BadRequest(message, code=code)


class KaiClosureRequestView(MobileAPIView):
    web_page = 'kai_request_closure'
    web_page_toggle = False   # @login_required only

    def post(self, request, report_id):
        report = party_case(request.user, report_id)
        block = closure_request_block(report)
        if block:
            _refuse(block)
        reason = _reason(request, 'reason', CLOSURE_REASON_REQUIRED)
        file_closure_request(report, request.user, reason, request._request)
        return Response({'message': CLOSURE_REQUESTED}, status=201)


class KaiDropRequestView(MobileAPIView):
    web_page = 'kai_request_drop'
    web_page_toggle = False   # @login_required only

    def post(self, request, report_id):
        # Only the submitter may withdraw a case (the website's lookup).
        report = party_case(request.user, report_id, submitted_by=request.user)
        block = drop_request_block(report)
        if block:
            _refuse(block)
        reason = _reason(request, 'reason', DROP_REASON_REQUIRED)
        file_drop_request(report, request.user, reason, request._request)
        return Response({'message': DROP_REQUESTED}, status=201)


class KaiAppealView(MobileAPIView):
    web_page = 'kai_file_appeal'
    web_page_toggle = False   # @login_required only

    def post(self, request, report_id):
        # Only the accused may appeal, so the lookup is the website's narrower one.
        report = party_case(request.user, report_id, targeted_to=request.user)
        allowed, why = KaiAppeal.can_file(report, request.user)
        if not allowed:
            raise BadRequest(why, code='appeal_unavailable')
        grounds = _reason(request, 'grounds', APPEAL_GROUNDS_REQUIRED)
        level = request.data.get('level') if hasattr(request.data, 'get') else None
        appeal = file_kai_appeal(report, request.user, level if isinstance(level, str) else 'chapter',
                                 grounds, request._request)
        return Response({'message': appeal_filed_message(appeal)}, status=201)


def appeal_levels():
    """The appeal form's "Appeal to" choices, in order."""
    return _choices(KaiAppeal.LEVEL_CHOICES)

