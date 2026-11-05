"""
The app's slating application (11-02-26) — applying for a position, natively.

* ``GET slating/periods/<id>/apply/`` — ``apply_view``'s form: the period's
  active form-builder questions (by section, with their options, validation
  rules and file limits), its active positions, and the member's draft (or
  withdrawn application) answers to carry on from.
* ``POST slating/periods/<id>/apply/`` — save it: ``action`` ``submit`` or
  ``save_draft``; ``reported_gpa`` and ``gpa_screenshot``;
  ``positions_first_choice`` / ``_second_choice`` / ``_third_choice`` /
  ``_do_not_want`` (lists of position ids); ``field_<id>`` per question (a
  list for multi-select and checkbox). Multipart when there's a file, else
  JSON. Saved by ``save_application`` (``src/view/slating/apply.py``), the
  website's own code: a submit with problems saves nothing and is a 400
  listing them; a draft keeps what validated and returns the rest in
  ``notes``.
* ``GET slating/my-applications/`` — ``my_applications``: every application
  this member has made, with its interview and what they can do with it.

The gates are the web's, in its order (``apply_refusal``): applications open,
the member may apply, and no application already past draft / withdrawn.
"""
import os

from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data
from src.mobile.views_slating import WITHDRAWABLE, _iso
from src.mobile.views_votes import VoteRefused
from src.models import SlatingApplication, SlatingPeriod
from src.view.slating.apply import TIERS, apply_refusal, field_sections, save_application

_REFUSAL_STATUS = {'closed': 409, 'ineligible': 403, 'already_applied': 409, 'committee_member': 403}


def _num_or_none(value):
    return float(value) if value is not None else None


def field_json(field):
    """One form-builder question, as the app draws it."""
    return {
        'id': field.id,
        'name': f'field_{field.id}',
        'label': field.label,
        'type': field.field_type,
        'placeholder': field.placeholder or '',
        'help_text': field.help_text or '',
        'options': [str(o) for o in (field.options or [])],
        'required': field.is_required,
        'section': field.section or 'General',
        'validation_rules': field.validation_rules or [],
        'allowed_file_types': field.allowed_file_types or [],
        'max_file_size_mb': field.max_file_size_mb,
        'depends_on_field': field.depends_on_field_id,
        'depends_on_value': field.depends_on_value or [],
    }


def response_value(response):
    """An answer's stored value: text, a number, a list, or a file's name."""
    if response.file_value:
        return {'file_name': os.path.basename(response.file_value.name)}
    if response.json_value is not None:
        return {'list': [str(v) for v in response.json_value] if isinstance(response.json_value, list)
                else [str(response.json_value)]}
    if response.number_value is not None:
        return {'number': float(response.number_value)}
    return {'text': response.text_value or ''}


def _preferences(application):
    prefs = (application.position_preferences or {}) if application else {}
    if isinstance(prefs, list):   # legacy: one wanted list
        prefs = {'first_choice': prefs}
    return {tier: [int(p) for p in prefs.get(tier, []) if str(p).isdigit()] for tier in TIERS}


class _ApplyBase(MobileAPIView):
    web_page = 'slating_apply'
    web_page_toggle = False   # @login_required + @pledge_page_allowed only

    def gate(self, request, period_id):
        period = get_object_or_404(SlatingPeriod, id=period_id)
        existing, refusal = apply_refusal(period, request.user)
        if refusal:
            code, message = refusal
            raise VoteRefused(f'apply_{code}' if code != 'already_applied' else code, message,
                              _REFUSAL_STATUS[code])
        return period, existing


class SlatingApplyView(_ApplyBase):
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request, period_id):
        period, existing = self.gate(request, period_id)
        form_fields = list(period.form_fields.filter(is_active=True).order_by('display_order'))
        positions = period.positions.filter(is_active=True).order_by('display_order')
        answers = {}
        if existing:
            for response in existing.responses.select_related('field').all():
                answers[str(response.field_id)] = response_value(response)
        return Response({
            'period': {
                'id': period.id,
                'name': period.name,
                'term': period.academic_term,
                'description': period.description,
                'nominations_close_at': _iso(period.nominations_close_at),
                'min_gpa': _num_or_none(period.min_gpa_requirement),
            },
            'sections': [{'title': title, 'fields': [field_json(f) for f in fields]}
                         for title, fields in field_sections(form_fields).items()],
            'positions': [{
                'id': p.id,
                'title': p.title,
                'description': p.description,
                'min_gpa': _num_or_none(p.min_gpa),
            } for p in positions],
            'application': {
                'id': existing.id,
                'status': existing.status,
                'status_label': existing.get_status_display(),
                'reported_gpa': _num_or_none(existing.reported_gpa),
                'has_gpa_screenshot': bool(existing.gpa_screenshot),
                'preferences': _preferences(existing),
            } if existing else None,
            'answers': answers,
            'web_path': reverse('slating_apply', args=[period.id]),
        })

    def post(self, request, period_id):
        period, existing = self.gate(request, period_id)
        data = _form_data(request)
        action = data.get('action') or 'save_draft'
        if action not in ('submit', 'save_draft'):
            raise BadRequest('action is submit or save_draft.', code='invalid_input')
        is_submit = action == 'submit'
        form_fields = period.form_fields.filter(is_active=True).order_by('display_order')
        positions = period.positions.filter(is_active=True).order_by('display_order')
        application, errors = save_application(period, request.user, existing, form_fields, positions,
                                                data, request.FILES, is_submit)
        if is_submit and errors:
            raise BadRequest('\n'.join(errors), code='invalid_input')
        return Response({
            'ok': True,
            'application_id': application.id,
            'status': application.status,
            'message': ('Your application has been submitted successfully!' if is_submit
                        else 'Your application has been saved as draft.'),
            # A draft keeps what validated; these answers weren't saved.
            'notes': errors,
        })


class SlatingMyApplicationsView(MobileAPIView):
    web_page = 'slating_my_applications'
    web_page_toggle = False   # @login_required only

    def get(self, request):
        apps = (SlatingApplication.objects.filter(applicant=request.user)
                .select_related('period').prefetch_related('interviews').order_by('-created_at'))
        rows = []
        for a in apps:
            interview = next(iter(a.interviews.all()), None)   # the web's app.interviews.first
            rows.append({
                'id': a.id,
                'period_id': a.period_id,
                'period': a.period.name,
                'period_status': a.period.status,
                'period_status_label': a.period.get_status_display(),
                'status': a.status,
                'status_label': a.get_status_display(),
                'submitted_at': _iso(a.submitted_at),
                'positions_count': len(a.get_preferred_positions()),
                'reported_gpa': _num_or_none(a.reported_gpa),
                'gpa_verified': a.gpa_verified,
                'interview': {
                    'scheduled_at': _iso(interview.scheduled_at),
                    'location': interview.location,
                    'completed': interview.completed_at is not None,
                } if interview else None,
                # The web's "Continue Application" (a draft, while the form is open).
                'can_continue': a.status == 'draft' and a.period.can_apply(),
                # The web's Withdraw button, where withdraw_application accepts it.
                'can_withdraw': a.status in WITHDRAWABLE and a.period.status == 'nominations_open',
            })
        return Response({'applications': rows})
