"""
Service hours setup in the app (batch 5): the website's VPP pages for
periods, member expectations and service events (``src/view/service_hours.py``),
so the officer running service hours no longer drops into the website for
them. The Vice President of Programming and admins only, exactly
``vpp_required`` (``user_is_vpp``), like the review screens
(``views_service_review.py``).

Periods (``manage_service_periods`` / ``edit_service_period``):

* ``GET  officer/service-hours/periods/`` — every period, newest first.
  ``POST`` — a new one (``ServicePeriodForm``: ``name``, ``start_date``,
  ``end_date``, ``default_hours_required``, ``requires_approval``,
  ``is_active``). 201 ``{period, message}``.
* ``GET  officer/service-hours/periods/<id>/`` — one; ``POST`` saves the same
  fields.
* ``POST officer/service-hours/periods/<id>/toggle-active/`` — activate or
  deactivate it.
* ``POST officer/service-hours/periods/<id>/delete/`` — delete it with its
  submissions; 400 ``has_service_events`` while a service event counts toward
  it.

Expectations (``manage_member_expectations``):

* ``GET  officer/service-hours/periods/<id>/expectations/`` — the custom
  requirements and the members still on the default. ``POST``
  ``{member, expected_hours, reason}`` adds one.
* ``DELETE officer/service-hours/periods/<id>/expectations/<eid>/`` — the
  member goes back to the default.

Service events (``service_events_list``, ``create_service_event``,
``service_event_detail``, ``edit_service_event``, ``delete_service_event``,
``service_event_attendance``, ``finalize_service_event``):

* ``GET  officer/service-hours/events/`` — upcoming (soonest first) and the 20
  latest past, plus the active periods the form offers. ``POST`` schedules one
  (the form's fields; ``date_time`` ISO 8601, a naive one being the chapter's
  wall clock; the reminder checkboxes as booleans). 201.
* ``GET  officer/service-hours/events/<id>/`` — the event, its roster with
  each member's attendance status and the counts. ``POST`` saves an edit;
  ``DELETE`` deletes it with its calendar event. Both 400
  ``already_finalized`` once hours are applied.
* ``GET  officer/service-hours/events/<id>/attendance/`` — the check-off list
  with each member's hours override. ``POST {action: save|finalize, present:
  [user_id…], hours_override: {user_id: hours}}`` — saves who came and, for
  ``finalize``, credits the hours (400 ``none_present`` with nobody checked,
  after saving, as the page does).
* ``POST officer/service-hours/events/<id>/finalize/`` — the detail page's
  "Finalize & Apply Hours".

Every write goes through the web views' own helpers, so the checks, the
pre-approved submissions finalizing creates, the reminder re-arming on a date
change and the activity log rows are the website's.
"""
from decimal import Decimal

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from src.forms import ServicePeriodForm
from src.mobile.api import check_web_page
from src.mobile.views_directory import photo_path
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_service_hours import _form_data, _form_errors
from src.mobile.views_service_review import _ServiceReviewView, _iso, _num
from src.models import ServiceMemberExpectation, ServicePeriod
from src.view import service_hours as web

#: The attendance POST's present list, at most (a roster is a few dozen).
MAX_PRESENT = 1000


def _period_json(p):
    out = {
        'id': p.id,
        'name': p.name,
        'start_date': p.start_date.isoformat(),
        'end_date': p.end_date.isoformat(),
        'default_hours_required': _num(p.default_hours_required),
        'requires_approval': p.requires_approval,
        'is_active': p.is_active,
        'is_current': p.is_current(),
    }
    if hasattr(p, 'submission_count'):
        out['submission_count'] = p.submission_count
        out['service_event_count'] = p.service_event_count
    return out


def _periods():
    return (ServicePeriod.objects.all().order_by('-start_date')
            .annotate(submission_count=Count('submissions', distinct=True),
                      service_event_count=Count('service_events', distinct=True)))


def _period(period_id):
    return get_object_or_404(_periods(), id=period_id)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


class _PeriodForm:
    """``ServicePeriodForm`` over the app's JSON (booleans as checkboxes)."""

    @staticmethod
    def save(request, instance=None):
        form = ServicePeriodForm(_form_data(request), instance=instance)
        if not form.is_valid():
            raise BadRequest(_form_errors(form), code='invalid_input')
        period = form.save(commit=False)
        if instance is None:
            period.created_by = request.user
        period.save()
        return period


class ServicePeriodListView(_ServiceReviewView):
    web_page = 'manage_service_periods'
    parser_classes = [JSONParser]

    def get(self, request):
        return Response({'periods': [_period_json(p) for p in _periods()]})

    def post(self, request):
        period = _PeriodForm.save(request)
        return Response({'period': _period_json(_period(period.id)),
                         'message': f'Created period: {period.name}'}, status=201)


class ServicePeriodView(_ServiceReviewView):
    web_page = 'edit_service_period'
    parser_classes = [JSONParser]

    def get(self, request, period_id):
        return Response({'period': _period_json(_period(period_id))})

    def post(self, request, period_id):
        period = _PeriodForm.save(request, instance=get_object_or_404(ServicePeriod, id=period_id))
        return Response({'period': _period_json(_period(period.id)),
                         'message': f'Updated period: {period.name}'})


class ServicePeriodToggleView(_ServiceReviewView):
    web_page = 'manage_service_periods'

    def post(self, request, period_id):
        period = get_object_or_404(ServicePeriod, id=period_id)
        status = 'activated' if web.toggle_service_period(period) else 'deactivated'
        return Response({'period': _period_json(_period(period.id)), 'message': f'{period.name} {status}.'})


class ServicePeriodDeleteView(_ServiceReviewView):
    web_page = 'manage_service_periods'

    def post(self, request, period_id):
        period = get_object_or_404(ServicePeriod, id=period_id)
        name = period.name
        error = web.delete_service_period(period)
        if error:
            raise BadRequest(error, code='has_service_events')
        return Response({'ok': True, 'message': f'Deleted period: {name}'})


# ── Expectations ─────────────────────────────────────────────────────────────

def _expectations_response(period):
    expectations, available = web.expectations_data(period)
    default = period.default_hours_required
    return Response({
        'period': _period_json(period),
        'expectations': [{
            'id': x.id,
            'member': {'user_id': x.member_id, 'name': x.member.name, 'member_type': x.member.member_type,
                       'photo': photo_path(x.member)},
            'expected_hours': _num(x.expected_hours),
            # The page's "N fewer / +N more than default".
            'difference': _num(x.expected_hours - default),
            'reason': x.reason or '',
            'created_by': x.created_by.name if x.created_by_id else None,
            'created_at': _iso(x.created_at),
        } for x in expectations],
        'available_members': [{'user_id': m.user_id, 'name': m.name, 'member_type': m.member_type}
                              for m in available.only('user_id', 'name', 'member_type')],
    })


class ServiceExpectationListView(_ServiceReviewView):
    web_page = 'manage_member_expectations'
    parser_classes = [JSONParser]

    def get(self, request, period_id):
        return _expectations_response(_period(period_id))

    def post(self, request, period_id):
        period = _period(period_id)
        body = _body(request)
        member_id = str(body.get('member') or '').strip()
        hours = str(body.get('expected_hours') if body.get('expected_hours') is not None else '').strip()
        reason = str(body.get('reason') or '')[:2000]
        expectation, error = web.add_member_expectation(period, member_id, hours, reason, request.user)
        if error:
            raise BadRequest(error, code='invalid_input')
        response = _expectations_response(period)
        response.data['message'] = f'Set {hours} hours for {expectation.member.name}.'
        response.status_code = 201
        return response


class ServiceExpectationView(_ServiceReviewView):
    web_page = 'manage_member_expectations'

    def delete(self, request, period_id, expectation_id):
        period = _period(period_id)
        expectation = get_object_or_404(ServiceMemberExpectation, id=expectation_id, period=period)
        name = expectation.member.name
        expectation.delete()
        response = _expectations_response(period)
        response.data['message'] = (f'Removed custom expectation for {name}. They will use the default '
                                    f'({period.default_hours_required} hours).')
        return response


# ── Service events ───────────────────────────────────────────────────────────

def _event_json(se):
    event = se.event
    return {
        'id': se.id,
        'event_id': event.id,
        'title': event.title,
        'description': event.description or '',
        'location': event.location or '',
        'date_time': timezone.localtime(event.date_time).isoformat(),
        'is_active': event.is_active,
        'period': {'id': se.period_id, 'name': se.period.name},
        'hours_awarded': _num(se.hours_awarded),
        'hours_applied': se.hours_applied,
        'reminder_1_enabled': event.reminder_1_enabled,
        'reminder_1_hours_before': event.reminder_1_hours_before,
        'reminder_1_sent': bool(event.reminder_1_sent_at),
        'reminder_2_enabled': event.reminder_2_enabled,
        'reminder_2_hours_before': event.reminder_2_hours_before,
        'reminder_2_sent': bool(event.reminder_2_sent_at),
        'email_reminder_enabled': se.email_reminder_enabled,
        'email_reminder_hours_before': se.email_reminder_hours_before,
        'email_reminder_subject': se.email_reminder_subject or '',
        'email_reminder_body': se.email_reminder_body or '',
        'email_reminder_sent_at': _iso(se.email_reminder_sent_at),
    }


def _form_periods():
    """The form's period dropdown: the active periods, newest first."""
    return [{'id': p.id, 'name': p.name, 'start_date': p.start_date.isoformat(), 'end_date': p.end_date.isoformat()}
            for p in ServicePeriod.objects.filter(is_active=True).order_by('-start_date')]


def _event_form(request, creating):
    values, errors = web.parse_service_event_form(_form_data(request), creating=creating)
    if errors:
        raise BadRequest(' '.join(errors), code='invalid_input')
    return values


def _service_event(service_event_id):
    return get_object_or_404(web.service_event_queryset(), id=service_event_id)


def _not_finalized(se, message):
    if se.hours_applied:
        raise BadRequest(message, code='already_finalized')


class ServiceEventListView(_ServiceReviewView):
    web_page = 'service_events_list'
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method == 'POST':
            check_web_page(request.user, 'create_service_event', toggle=False)

    def get(self, request):
        upcoming, past = web.service_events_lists()
        return Response({
            'upcoming': [_event_json(se) for se in upcoming],
            'past': [_event_json(se) for se in past],
            'periods': _form_periods(),
        })

    def post(self, request):
        values = _event_form(request, creating=True)
        se = web.create_service_event_record(values, request.user, request._request)
        return Response({'service_event': _event_json(_service_event(se.id)),
                         'message': f'Service event "{values["title"]}" created.'}, status=201)


def _detail_response(se, message=None):
    member_data, counts = web.service_event_roster(se)
    out = {
        'service_event': _event_json(se),
        'created_by': se.created_by.get_display_name() if se.created_by_id else None,
        'members': [{
            'user_id': d['user'].user_id,
            'name': d['user'].get_display_name(),
            'photo': photo_path(d['user']),
            'status': d['status'],
            'marked_at': _iso(d['marked_at']),
        } for d in member_data],
        'counts': counts,
        'can_finalize': not se.hours_applied and counts['present'] > 0,
        'can_edit': not se.hours_applied,
        'periods': _form_periods(),
    }
    if message:
        out['message'] = message
    return Response(out)


class ServiceEventView(_ServiceReviewView):
    web_page = 'service_event_detail'
    parser_classes = [JSONParser]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.method == 'POST':
            check_web_page(request.user, 'edit_service_event', toggle=False)
        elif request.method == 'DELETE':
            check_web_page(request.user, 'delete_service_event', toggle=False)

    def get(self, request, service_event_id):
        return _detail_response(_service_event(service_event_id))

    def post(self, request, service_event_id):
        se = _service_event(service_event_id)
        _not_finalized(se, web.SERVICE_EVENT_FINALIZED)
        values = _event_form(request, creating=False)
        web.update_service_event_record(se, values)
        return _detail_response(_service_event(se.id), f'Service event "{values["title"]}" updated.')

    def delete(self, request, service_event_id):
        se = _service_event(service_event_id)
        _not_finalized(se, web.SERVICE_EVENT_NOT_DELETABLE)
        title = se.event.title
        se.event.delete()  # cascades to ServiceEvent via OneToOneField
        return Response({'ok': True, 'message': f'Service event "{title}" deleted.'})


def _attendance_response(se, members, message=None, count=None):
    overrides = se.member_hours_override or {}
    roster = web.service_attendance_roster(se, members)
    out = {
        'service_event': _event_json(se),
        'read_only': se.hours_applied,
        'members': [{
            'user_id': d['user'].user_id,
            'name': d['user'].get_display_name(),
            'photo': photo_path(d['user']),
            'present': d['present'],
            'hours_override': _num(Decimal(overrides[d['user'].user_id]))
            if overrides.get(d['user'].user_id) else None,
        } for d in roster],
        'present_count': sum(1 for d in roster if d['present']),
    }
    if message:
        out['message'] = message
    if count is not None:
        out['count'] = count
    return Response(out)


def _present_ids(body):
    raw = body.get('present', [])
    if not isinstance(raw, list) or len(raw) > MAX_PRESENT:
        raise BadRequest('present must be a list of member ids.', code='invalid_input')
    return {str(uid) for uid in raw if isinstance(uid, (str, int)) and not isinstance(uid, bool)}


def _override_fields(body):
    """``{user_id: hours}`` → the page's ``hours_override_<user_id>`` fields."""
    raw = body.get('hours_override') or {}
    if not isinstance(raw, dict):
        raise BadRequest('hours_override must map member ids to hours.', code='invalid_input')
    return {f'hours_override_{uid}': str(v) for uid, v in raw.items()
            if v is not None and not isinstance(v, (bool, dict, list))}


class ServiceEventAttendanceView(_ServiceReviewView):
    web_page = 'service_event_attendance'
    parser_classes = [JSONParser]

    def get(self, request, service_event_id):
        se = _service_event(service_event_id)
        return _attendance_response(se, web.service_attendance_members())

    def post(self, request, service_event_id):
        se = _service_event(service_event_id)
        _not_finalized(se, 'Hours have already been applied for this event.')
        body = _body(request)
        action = body.get('action')
        if action not in ('save', 'finalize'):
            raise BadRequest('Save or finalize.', code='invalid_input')
        present_ids = _present_ids(body)
        members = web.service_attendance_members()
        web.save_service_event_attendance(se, members, present_ids, _override_fields(body), request.user)
        if action == 'save':
            web.log_service_attendance_saved(se, len(present_ids), request.user, request._request)
            return _attendance_response(
                _service_event(se.id), members,
                f'Attendance saved — {len(present_ids)} member(s) marked present.')
        if not present_ids:
            raise BadRequest(web.NEEDS_SOMEONE_PRESENT, code='none_present')
        count = web.finalize_service_event_hours(se, request.user, request._request)
        return _attendance_response(_service_event(se.id), members,
                                    web.service_event_finalized_message(se, count), count)


class ServiceEventFinalizeView(_ServiceReviewView):
    web_page = 'finalize_service_event'

    def post(self, request, service_event_id):
        se = _service_event(service_event_id)
        _not_finalized(se, 'Hours have already been applied for this event.')
        count = web.finalize_service_event_hours(se, request.user, request._request)
        response = _detail_response(_service_event(se.id), web.service_event_finalized_message(se, count))
        response.data['count'] = count
        return response

