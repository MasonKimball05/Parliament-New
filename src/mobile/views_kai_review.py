"""
The Kai Committee's review side in the app (batch 6): the website's case list
(``view_kai_reports``, /kai/reports/), bulk actions
(``bulk_actions_kai_reports``), the case page (``manage_kai_report``,
/kai/reports/<id>/), the recusal and stand-in forms on it
(``recuse_kai_member``, ``end_kai_recusal``, ``appoint_kai_standin``,
``remove_kai_standin``), the report templates (``manage_kai_templates``,
``create_kai_template``, ``edit_kai_template``, ``delete_kai_template``) and
the committee's member grants (``manage_kai_permissions``,
``update_kai_member_permission``, ``reset_kai_permissions``).

Every read and write goes through the helpers those pages call
(src/view/kai_reports.py, src/view/committee/manage_kai_permissions.py), so
who may see a case, what is redacted, what is written, logged and emailed are
the website's by construction:

* ``GET kai/review/?status=&category=&search=&date_from=&date_to=&assigned=``
  — the list (``kai_list_scope`` / ``kai_report_list`` / ``kai_list_stats``):
  anyone with ``can_view_report_list``, or a stand-in (their own cases only).
  Each row is redacted as the list card is: no description without
  ``can_view_report_details``, "Anonymous" / "Redacted" for an identity the
  viewer may not see, and for a case the viewer is a party to, neither the
  description nor either name (the card's recusal notice instead). The search
  box is the page's permission-gated predicate, narrowed to title and tags on
  those rows. The analytics and the cross-case activity feed go to holders of
  the list grant only (the page also shows them to a pure stand-in).
* ``POST kai/review/bulk/`` ``{action, report_ids}`` — ``mark_reviewed``,
  ``mark_pending`` or ``archive`` (``apply_kai_bulk_action``). The CSV export
  stays on the website.
* ``GET kai/review/<id>/`` — one case (``kai_case_access`` /
  ``kai_case_context``), for a viewer the page lets in. The accused, and
  anyone manually recused, gets 403 ``recused``: nothing about the case. Each
  block is sent only when the page renders it for this viewer (see
  ``_case_json``).
* ``POST kai/review/<id>/`` ``{action, …}`` — the page's actions
  (``apply_kai_case_action``), checked against the case-narrowed access first:
  403 ``forbidden`` for an action the viewer may not take, 400
  ``invalid_input`` with the page's own words when it refuses the input.
  Answers the case again (or ``{recused: true}`` if the change recused the
  viewer) with ``messages`` — what the page would flash.
* ``POST kai/review/<id>/recuse/`` ``{members: [{member, reason, replacement}],
  notes}``, ``…/recuse/end/`` ``{recusal_id}``, ``…/standin/``
  ``{recusal_id, replacement}``, ``…/standin/remove/`` ``{recusal_id}`` — the
  head of Kai only (``_can_appoint_standins``), never while recused.
* ``GET/POST kai/templates/``, ``GET/POST kai/templates/<id>/``,
  ``POST kai/templates/<id>/delete/`` — Kai chairs (``Committee.is_chair``)
  and site admins, as the pages allow.
* ``GET committees/<code>/kai-permissions/``, ``POST …/<user_id>/`` (the seven
  flags), ``POST …/reset/`` — the Kai committee's actual chairs only
  (``_is_kai_chair``; never an admin). No feature flag, as on the website.

The emails, their tracking pixels and the print view stay the website's; the
attachment and uploaded answers are site paths, streamed by its Kai-gated file
views. None of the Kai pages has ``@require_page_enabled``; each page's pledge
restriction applies. Nothing here may be cached on the phone.
"""
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.text import Truncator
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView, check_web_page
from src.mobile.views_excuses_polls import BadRequest
from src.models import Committee, KaiRecusal, KaiReport, KaiReportTemplate, ParliamentUser
from src.view import kai_reports as web
from src.view.committee import manage_kai_permissions as web_perms

#: Longest free text the app may send (notes, messages, reasons).
TEXT_MAX = 20000
#: Most cases one bulk action may name (the list shows at most KAI_LIST_LIMIT).
BULK_MAX = web.KAI_LIST_LIMIT

#: The case page's actions that do something (``add_activity`` has no branch there).
CASE_ACTIONS = (web.KAI_EDIT_ACTIONS | web.KAI_ACTIVITY_ACTIONS | web.KAI_CLOSE_ACTIONS) - {'add_activity'}
#: The case page's script: outcomes that show the committee-notes box, and that
#: offer "Minutes closed by accused request".
OUTCOMES_WITH_NOTES = ('heard', 'warning_issued', 'sanctions_applied', 'mediation', 'referred')
OUTCOMES_WITH_MINUTES_CLOSED = ('heard', 'sanctions_applied')
BULK_ACTIONS = ('mark_reviewed', 'mark_pending', 'archive')


def _iso(value):
    return value.isoformat() if value else None


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _messages(notes):
    return [{'level': level, 'text': text} for level, text in notes]


def _require_kai():
    if not check_feature_enabled('kai_reports'):
        raise exceptions.PermissionDenied('Kai reports are off.', code='feature_disabled')


def _kai_committee():
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None:
        raise exceptions.NotFound('Kai committee not found.')
    return committee


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _text(body, key, required=False, label=None, max_length=TEXT_MAX):
    value = body.get(key)
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise BadRequest(f'{label or key} must be text.', code='invalid_input')
    if len(value) > max_length:
        raise BadRequest(f'{label or key} is too long.', code='invalid_input')
    if required and not value.strip():
        raise BadRequest(f'{label or key} is required.', code='invalid_input')
    return value


def _int(body, key, label):
    value = body.get(key)
    if isinstance(value, bool) or value in (None, ''):
        raise BadRequest(f'Choose {label}.', code='invalid_input')
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadRequest(f'Choose {label}.', code='invalid_input')


def _query(pairs):
    """A QueryDict, as the website's forms would post ``pairs`` (lists repeat)."""
    data = QueryDict(mutable=True)
    for key, value in pairs.items():
        if isinstance(value, (list, tuple)):
            data.setlist(key, [str(v) for v in value])
        elif value is not None:
            data[key] = str(value)
    return data


def _who(user):
    return {'user_id': user.pk, 'name': user.name} if user is not None else None


# ── The list ─────────────────────────────────────────────────────────────────

def _row_json(report, kai_access):
    """One list card (templates/kai/view_reports.html), redacted as it is."""
    recusal = report.viewer_recusal
    if recusal:
        description = None
    elif kai_access['can_view_report_details']:
        description = Truncator(report.description or '').words(30, truncate=' …')
    else:
        description = None
    show_submitter = kai_access['can_view_submitter_identity'] and recusal != 'accused'
    show_accused = kai_access['can_view_accused_identity'] and recusal != 'accused'
    return {
        'id': report.id,
        'number': report.display_number,
        'title': report.title,
        'status': report.status,
        'status_label': report.get_status_display(),
        'outcome': report.deliberation_outcome,
        'outcome_label': report.get_deliberation_outcome_display(),
        'category': report.category,
        'category_label': report.get_category_display(),
        'closed_by_accused_request': report.closed_by_accused_request,
        'is_stale': report.is_stale,
        'days_open': report.days_open,
        'assigned_to': report.assigned_to.name if report.assigned_to_id else None,
        # 'accused' / 'submitter' / 'self' when the viewer is a party; the card
        # shows a notice instead of the description.
        'viewer_recusal': recusal,
        'description': description,
        'submitter': report.submitter_display_name if show_submitter else 'Anonymous',
        'accused': (report.accused_display_name if show_accused else 'Redacted') if report.targeted_to_id else None,
        'submitted_at': _iso(report.submitted_at),
        'has_attachment': bool(report.attachment),
        'tags': report.get_tag_labels(),
        # The card offers no checkbox and no Manage button on the viewer's own case.
        'can_open': recusal != 'accused',
    }


def _access_json(kai_access):
    return {
        **{f: bool(kai_access.get(f)) for f in web._KAI_PERMISSION_FIELDS},
        'is_break_glass': bool(kai_access.get('is_break_glass')),
        'break_glass_expires_at': _iso(kai_access.get('break_glass_expires_at')),
    }


class KaiReviewListView(MobileAPIView):
    web_page = 'view_kai_reports'
    web_page_toggle = False   # @require_feature_flag, no page toggle

    def get(self, request):
        _require_kai()
        user = request.user
        committee = _kai_committee()
        kai_access, standin_case_ids = web.kai_list_scope(user, committee)
        if not kai_access['can_view_report_list'] and not standin_case_ids:
            raise exceptions.PermissionDenied('You do not have permission to view Kai reports.', code='forbidden')

        listing = web.kai_report_list(user, kai_access, standin_case_ids, request.query_params)
        # One deliberate tightening: the page's analytics panel (outcome totals
        # and the last eight activity rows across EVERY case) renders for a pure
        # stand-in too, who may only see their own cases. The app sends it to
        # holders of the list grant only.
        if kai_access['can_view_report_list']:
            stats = web.kai_list_stats(user, kai_access, listing['cat_map'])
        else:
            stats = {'category_data': {}, 'monthly_data': {}, 'outcome_pending': 0, 'outcome_heard': 0,
                     'outcome_thrown_out': 0, 'recent_activities': []}
        oldest = listing['oldest_pending']
        _templates_committee, can_templates = web.kai_template_access(user)
        return Response({
            'reports': [_row_json(r, kai_access) for r in listing['reports']],
            'truncated': listing['reports_truncated'],
            'limit': web.KAI_LIST_LIMIT,
            'total': listing['total_reports'],
            'filters_active': listing['filters_active'],
            'filters': {
                'status': listing['status_filter'],
                'category': listing['category_filter'],
                'search': listing['search_query'],
                'date_from': listing['date_from'],
                'date_to': listing['date_to'],
                'assigned': listing['assigned_filter'],
            },
            'search_placeholder': web._kai_search_placeholder(kai_access),
            'counts': listing['counts'],
            'category_counts': listing['category_counts'],
            'assigned_counts': listing['assigned_counts'],
            'stale_count': listing['stale_count'],
            'stale_after_days': KaiReport.STALE_AFTER_DAYS,
            'oldest_pending': ({'id': oldest.id, 'number': oldest.display_number, 'title': oldest.title,
                                'days_open': oldest.days_open} if oldest else None),
            'categories': _choices(KaiReport.CATEGORY_CHOICES),
            'statuses': _choices(KaiReport.STATUS_CHOICES),
            'stats': {
                'categories': stats['category_data'],
                'months': stats['monthly_data'],
                'outcomes': {
                    'pending': stats['outcome_pending'],
                    'heard': stats['outcome_heard'],
                    'thrown_out': stats['outcome_thrown_out'],
                },
            },
            'recent_activity': [{
                'action': a.action,
                'label': a.get_action_display(),
                'report_id': a.report_id,
                'report_title': a.report.title,
                'actor': a.display_actor or 'System',
                'timestamp': _iso(a.timestamp),
            } for a in stats['recent_activities']],
            'access': _access_json(kai_access),
            'standin_only': not kai_access['can_view_report_list'],
            'bulk_actions': ([a for a, ok in (('mark_reviewed', kai_access['can_edit_open_cases']),
                                              ('mark_pending', kai_access['can_edit_open_cases']),
                                              ('archive', kai_access['can_close_cases'])) if ok]
                             if kai_access['can_view_report_list'] else []),
            'can_manage_permissions': bool(web._is_kai_chair(user, committee)),
            'can_manage_templates': can_templates,
            'committee_code': committee.code,
            'links': {
                'page': reverse('view_kai_reports'),
                'export': reverse('export_kai_reports_csv'),
                'form_builder': reverse('kai_form_builder'),
            },
        })


class KaiReviewBulkView(MobileAPIView):
    web_page = 'bulk_actions_kai_reports'
    web_page_toggle = False

    def post(self, request):
        _require_kai()
        committee = _kai_committee()
        kai_access = web._get_kai_access(request.user, committee)
        if not kai_access['can_view_report_list']:
            raise exceptions.PermissionDenied('You do not have permission to perform bulk actions.',
                                              code='forbidden')
        body = _body(request)
        action = body.get('action')
        if action == 'export_csv':
            raise BadRequest('Export the CSV from the website.', code='invalid_input')
        if action not in BULK_ACTIONS:
            raise BadRequest('Choose an action.', code='invalid_input')
        ids = body.get('report_ids')
        if not isinstance(ids, list) or not ids or len(ids) > BULK_MAX \
                or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise BadRequest('Select the reports to change.', code='invalid_input')
        refusal = web.kai_case_action_refusal(action, kai_access)
        if refusal:
            raise exceptions.PermissionDenied(refusal, code='forbidden')
        notes, _response = web.apply_kai_bulk_action(request._request, kai_access, ids, action)
        if notes.errors:
            raise BadRequest(' '.join(notes.errors), code='invalid_input')
        return Response({'messages': _messages(notes), 'message': notes[0][1] if notes else ''})


# ── One case ─────────────────────────────────────────────────────────────────

def _response_json(resp):
    """A custom answer as the case page renders it."""
    ftype = resp.field.field_type
    if ftype == 'file' and resp.file_value:
        return {'label': resp.field.label, 'value': None, 'file': reverse('kai_response_file', args=[resp.id])}
    if ftype in ('multiselect', 'checkbox') and resp.json_value:
        values = resp.json_value if isinstance(resp.json_value, (list, tuple)) else [resp.json_value]
        return {'label': resp.field.label, 'value': ', '.join(str(v) for v in values), 'file': None}
    if resp.number_value:
        return {'label': resp.field.label, 'value': str(resp.number_value), 'file': None}
    return {'label': resp.field.label, 'value': resp.text_value or '-', 'file': None}


def _case_json(user, report, committee, kai_access):
    """
    The case page for this viewer. Every block is sent exactly when
    templates/kai/manage_report.html (and its partials) renders it, so a
    field the page withholds from this viewer never leaves the server.
    """
    ctx = web.kai_case_context(user, report, committee, kai_access)
    can_edit = bool(kai_access['can_edit_open_cases'])
    can_activity = bool(kai_access['can_add_activity'])
    can_close = bool(kai_access['can_close_cases'])
    see_submitter = bool(kai_access['can_view_submitter_identity'])
    see_accused = bool(kai_access['can_view_accused_identity'])
    can_appoint = bool(ctx['can_appoint_standins'])

    # "Notify Accused Person" / the accused picker: edit + accused identity.
    accused_block = None
    if can_edit and see_accused:
        accused = report.targeted_to
        accused_block = {
            'accused': ({'user_id': accused.pk, 'name': report.accused_display_name, 'email': accused.email or ''}
                        if accused else None),
            'notified': report.accused_notified,
            'notified_at': _iso(report.accused_notified_at),
            'notification_message': report.accused_notification_message or '',
            'email_viewed_at': _iso(report.accused_email_viewed_at),
            'members': [{'user_id': m.pk, 'name': m.name, 'member_status': m.member_status,
                         'email': m.email or ''}
                        for m in ctx['all_members'].only('pk', 'name', 'member_status', 'email')],
        }
    # "Notify Submitter": open minutes, edit + submitter identity.
    submitter_block = None
    if not report.closed_by_accused_request and can_edit and see_submitter:
        submitter_block = {
            'email': report.submitted_by.email or '' if report.submitted_by_id else '',
            'notified_at': _iso(report.submitter_notified_at),
            'email_viewed_at': _iso(report.submitter_email_viewed_at),
        }

    recusals = None
    if ctx['recusals'] or can_appoint:
        recusals = {
            'seats': [{
                'id': r.id,
                'member': r.member_label,   # v4.4.2: party_label
                'reason': r.reason,
                'reason_label': r.get_reason_display(),
                'recorded_by': (r.recorded_by.name if r.recorded_by_id and r.reason != 'accused' else None),
                'notes': r.notes or '',
                'replacement': r.replacement.name if r.replacement_id else None,
                'appointed_at': _iso(r.appointed_at),
                # The panel offers "end this recusal" for a manual reason only.
                'can_end': can_appoint and r.reason not in ('accused', 'submitter'),
            } for r in ctx['recusals']],
            'can_appoint': can_appoint,
            'eligible_standins': [{'user_id': m.pk, 'name': m.name, 'member_type': m.member_type}
                                  for m in ctx['eligible_standins']],
            'recusable_members': [{'user_id': m.pk, 'name': m.name, 'is_me': m.pk == user.pk}
                                  for m in ctx['recusable_members']],
            'reasons': [{'value': v, 'label': str(label)} for v, label in KaiRecusal.REASON_CHOICES
                        if v in KaiRecusal.MANUAL_REASONS] if can_appoint else [],
        }

    return {
        'id': report.id,
        'number': report.display_number,
        'title': report.title,
        'status': report.status,
        'status_label': report.get_status_display(),
        'outcome': report.deliberation_outcome,
        'outcome_label': report.get_deliberation_outcome_display(),
        'category': report.category,
        'category_label': report.get_category_display(),
        'is_stale': report.is_stale,
        'days_open': report.days_open,
        'closed_by_accused_request': report.closed_by_accused_request,
        'description': report.description,
        # "Directed To": present or not; the name only with the identity grant.
        'has_accused': bool(report.targeted_to_id),
        'accused_name': report.accused_display_name if report.targeted_to_id and see_accused else None,
        'submitter_name': report.submitter_display_name if see_submitter else None,
        'attachment': reverse('kai_report_attachment', args=[report.id]) if report.attachment else None,
        'responses': [_response_json(r) for r in ctx['custom_responses']],
        'submitted_at': _iso(report.submitted_at),
        'reviewed_by': report.reviewed_by.name if report.reviewed_by_id else None,
        'reviewed_at': _iso(report.reviewed_at),
        'assigned_to': _who(report.assigned_to) if report.assigned_to_id else None,
        'tags': report.get_tags_list(),
        'tag_labels': report.get_tag_labels(),
        'legacy_tags': ctx['legacy_tags'],
        'tag_choices': _choices(ctx['tag_choices']),
        'chair_notes': report.chair_notes if can_activity else None,
        'committee_notes': report.committee_notes if can_edit else None,
        'outcomes': _choices(KaiReport.DELIBERATION_CHOICES),
        'outcomes_with_notes': list(OUTCOMES_WITH_NOTES),
        'outcomes_with_minutes_closed': list(OUTCOMES_WITH_MINUTES_CLOSED),
        'accused_section': accused_block,
        'submitter_section': submitter_block,
        'closure_requests': ([{
            'id': cr.id,
            'type': cr.request_type,
            'type_label': 'Drop Request' if cr.request_type == 'drop' else 'Closure Request',
            'requested_by': cr.requester_label,   # v4.4.2: party_label
            'requested_at': _iso(cr.requested_at),
            'reason': cr.reason,
            'status': cr.status,
            'status_label': cr.get_status_display(),
            'review_notes': cr.review_notes,
            'reviewed_by': cr.reviewed_by.name if cr.reviewed_by_id else None,
            'reviewed_at': _iso(cr.reviewed_at),
        } for cr in ctx['closure_requests']] if can_close else []),
        'related': [{
            'id': r.id,
            'title': r.title,
            'submitter': r.submitter_display_name if see_submitter else 'Anonymous',
            'submitted_at': _iso(r.submitted_at),
        } for r in ctx['related_reports']],
        'linkable': [{'id': r.id, 'title': Truncator(r.title).chars(40)} for r in ctx['available_reports']],
        'assignable': [_who(m) for m in ctx['assignable_members']],
        'activity': [{
            'action': a.action,
            'label': a.get_action_display(),
            'actor': a.display_actor,
            'details': a.display_details,
            'timestamp': _iso(a.timestamp),
        } for a in ctx['activity_log']],
        'recusals': recusals,
        'access': {
            **_access_json(kai_access),
            'recusal_reason': kai_access.get('recusal_reason'),
            'is_standin': bool(kai_access.get('is_standin')),
            'is_submitter_readonly': bool(kai_access.get('is_submitter_readonly')),
        },
        'can': {
            'edit': can_edit,
            'notes': can_activity,
            'close': can_close,
            'archive': can_close and report.status != 'archived',
            'notify_accused': can_edit and see_accused,
            'notify_submitter': submitter_block is not None,
            'appoint': can_appoint,
        },
        'links': {
            'page': reverse('manage_kai_report', args=[report.id]),
            'print': reverse('print_kai_report', args=[report.id]),
        },
    }


def _open_case(user, report_id):
    """``(report, committee, kai_access)`` as the case page lets ``user`` in, or the refusal."""
    _require_kai()
    report = get_object_or_404(KaiReport, id=report_id)
    committee = _kai_committee()
    kai_access, refusal, message = web.kai_case_access(user, report, committee)
    if refusal == 'recused':
        raise exceptions.PermissionDenied(message, code='recused')
    if refusal:
        raise exceptions.PermissionDenied(message, code='forbidden')
    return report, committee, kai_access


def _after(user, report_id, notes):
    """The answer to a change: the case again, or ``recused`` if the change took the viewer off it."""
    payload = {'messages': _messages(notes),
               'message': next((t for level, t in notes if level == 'success'), notes[0][1] if notes else '')}
    report = KaiReport.objects.filter(id=report_id).first()
    if report is None:
        return Response({**payload, 'recused': True})
    committee = _kai_committee()
    kai_access, refusal, _message = web.kai_case_access(user, report, committee)
    if refusal:
        return Response({**payload, 'recused': True})
    return Response({**_case_json(user, report, committee, kai_access), **payload, 'recused': False})


def _case_form(action, body):
    """The posted fields for ``action``, validated, as the page's form posts them."""
    fields = {'action': action}
    if action == 'assign_case':
        raw = body.get('assigned_to')
        fields['assigned_to'] = '' if raw in (None, '') else str(raw)
    elif action == 'update_notes':
        fields['chair_notes'] = _text(body, 'chair_notes', label='Chair notes')
    elif action == 'update_tags':
        tags = body.get('tags')
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise BadRequest('Tags must be a list.', code='invalid_input')
        fields['tags'] = tags
    elif action == 'update_deliberation':
        outcome = body.get('deliberation_outcome')
        if outcome not in dict(KaiReport.DELIBERATION_CHOICES):
            raise BadRequest('Please select a deliberation outcome.', code='invalid_input')
        fields['deliberation_outcome'] = outcome
        fields['committee_notes'] = _text(body, 'committee_notes', label='Committee notes')
        if body.get('closed_by_accused_request') is True:
            fields['closed_by_accused_request'] = 'on'
    elif action in ('link_report', 'unlink_report'):
        fields['related_report_id'] = _int(body, 'related_report_id', 'a report')
    elif action == 'update_accused':
        accused_id = body.get('accused_id')
        fields['accused_id'] = '' if accused_id in (None, '') else str(accused_id)
        email = _text(body, 'accused_email', label='Email', max_length=254).strip()
        if email:
            try:
                validate_email(email)
            except ValidationError:
                raise BadRequest('Enter a valid email address.', code='invalid_input')
        fields['accused_email'] = email
    elif action == 'notify_accused':
        fields['accused_notification_message'] = _text(body, 'accused_notification_message', label='Message')
    elif action in ('approve_closure', 'deny_closure'):
        fields['closure_request_id'] = _int(body, 'closure_request_id', 'a request')
        fields['review_notes'] = _text(body, 'review_notes', label='Notes')
    return _query(fields)


class KaiReviewCaseView(MobileAPIView):
    web_page = 'manage_kai_report'
    web_page_toggle = False

    def get(self, request, report_id):
        report, committee, kai_access = _open_case(request.user, report_id)
        return Response(_case_json(request.user, report, committee, kai_access))

    def post(self, request, report_id):
        report, committee, kai_access = _open_case(request.user, report_id)
        body = _body(request)
        action = body.get('action')
        if action not in CASE_ACTIONS:
            raise BadRequest('Choose an action.', code='invalid_input')
        refusal = web.kai_case_action_refusal(action, kai_access)
        if refusal:
            raise exceptions.PermissionDenied(refusal, code='forbidden')
        data = _case_form(action, body)
        notes = web.apply_kai_case_action(request._request, report, committee, kai_access, data)
        if notes.errors:
            raise BadRequest(' '.join(notes.errors), code='invalid_input')
        return _after(request.user, report.id, notes)


# ── Recusals and stand-ins (the head of Kai) ─────────────────────────────────

class _HeadOfKaiView(MobileAPIView):
    web_page_toggle = False

    def gate(self, request, report_id):
        _require_kai()
        report = get_object_or_404(KaiReport, id=report_id)
        committee = _kai_committee()
        if web._is_recused_from(report, request.user):
            raise exceptions.PermissionDenied('You are recused from this case.', code='recused')
        if not web._can_appoint_standins(request.user, committee):
            raise exceptions.PermissionDenied('Only the head of Kai may do this.', code='forbidden')
        return report

    def finish(self, request, report_id, notes):
        if notes.errors:
            raise BadRequest(' '.join(notes.errors), code='invalid_input')
        return _after(request.user, report_id, notes)


class KaiRecuseView(_HeadOfKaiView):
    web_page = 'recuse_kai_member'

    def post(self, request, report_id):
        self.gate(request, report_id)
        body = _body(request)
        rows = body.get('members')
        if not isinstance(rows, list) or not rows or len(rows) > 50 or not all(isinstance(r, dict) for r in rows):
            raise BadRequest('Select at least one member to recuse.', code='invalid_input')
        members, reasons, replacements = [], [], []
        for row in rows:
            member = row.get('member')
            reason = row.get('reason') or 'unavailable'
            if not isinstance(member, str) or not member:
                raise BadRequest('Select at least one member to recuse.', code='invalid_input')
            if reason not in KaiRecusal.MANUAL_REASONS:
                raise BadRequest('Choose a reason.', code='invalid_input')
            replacement = row.get('replacement') or ''
            if not isinstance(replacement, str):
                raise BadRequest('Choose a stand-in.', code='invalid_input')
            members.append(member)
            reasons.append(reason)
            replacements.append(replacement)
        data = _query({'member': members, 'reason': reasons, 'replacement': replacements,
                       'notes': _text(body, 'notes', label='Notes')})
        notes, _where = web.recuse_kai_member_action(request._request, report_id, data)
        return self.finish(request, report_id, notes)


class KaiEndRecusalView(_HeadOfKaiView):
    web_page = 'end_kai_recusal'

    def post(self, request, report_id):
        self.gate(request, report_id)
        data = _query({'recusal_id': _int(_body(request), 'recusal_id', 'a recusal')})
        notes, _where = web.end_kai_recusal_action(request._request, report_id, data)
        return self.finish(request, report_id, notes)


class KaiAppointStandinView(_HeadOfKaiView):
    web_page = 'appoint_kai_standin'

    def post(self, request, report_id):
        self.gate(request, report_id)
        body = _body(request)
        replacement = body.get('replacement')
        if not isinstance(replacement, str) or not replacement:
            raise BadRequest('Choose a stand-in.', code='invalid_input')
        data = _query({'recusal_id': _int(body, 'recusal_id', 'a recusal'), 'replacement': replacement})
        notes, _where = web.appoint_kai_standin_action(request._request, report_id, data)
        return self.finish(request, report_id, notes)


class KaiRemoveStandinView(_HeadOfKaiView):
    web_page = 'remove_kai_standin'

    def post(self, request, report_id):
        self.gate(request, report_id)
        data = _query({'recusal_id': _int(_body(request), 'recusal_id', 'a recusal')})
        notes, _where = web.remove_kai_standin_action(request._request, report_id, data)
        return self.finish(request, report_id, notes)


# ── Report templates ─────────────────────────────────────────────────────────

def _template_json(t):
    return {
        'id': t.id,
        'name': t.name,
        'description': t.description,
        'category': t.category,
        'category_label': t.get_category_display(),
        'title_template': t.title_template,
        'description_template': t.description_template,
        'suggested_tags': list(t.suggested_tags or []),
        'is_active': t.is_active,
        'created_by': t.created_by.name if t.created_by_id else None,
        'created_at': _iso(t.created_at),
        'updated_at': _iso(t.updated_at),
    }


def _template_form(body):
    """The template form's fields, all required but the tags and ``is_active``."""
    fields = {}
    for key, label, max_length in (('name', 'Name', 200), ('description', 'Description', TEXT_MAX),
                                   ('title_template', 'Title template', 255),
                                   ('description_template', 'Description template', TEXT_MAX)):
        fields[key] = _text(body, key, required=True, label=label, max_length=max_length).strip()
    category = body.get('category')
    if category not in dict(KaiReport.CATEGORY_CHOICES):
        raise BadRequest('Choose a category.', code='invalid_input')
    fields['category'] = category
    tags = body.get('suggested_tags', [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise BadRequest('Suggested tags must be a list.', code='invalid_input')
    fields['suggested_tags'] = tags
    if body.get('is_active') is True:
        fields['is_active'] = 'on'
    return _query(fields)


class _TemplatesView(MobileAPIView):
    web_page_toggle = False

    def gate(self, request):
        _require_kai()
        committee, allowed = web.kai_template_access(request.user)
        if committee is None:
            raise exceptions.NotFound('Kai committee not found.')
        if not allowed:
            raise exceptions.PermissionDenied('Only Kai chairs can manage templates.', code='forbidden')

    @staticmethod
    def choices():
        return {'categories': _choices(KaiReport.CATEGORY_CHOICES), 'tags': _choices(KaiReport.TAG_CHOICES)}


class KaiTemplatesView(_TemplatesView):
    web_page = 'manage_kai_templates'

    def get(self, request):
        self.gate(request)
        templates = KaiReportTemplate.objects.select_related('created_by').order_by('-created_at', '-id')
        return Response({'templates': [_template_json(t) for t in templates], **self.choices(),
                         'links': {'form_builder': reverse('kai_form_builder')}})

    def post(self, request):
        """Create (``create_kai_template``, its own page with its own pledge restriction)."""
        check_web_page(request.user, 'create_kai_template', toggle=False)
        self.gate(request)
        template, notes = web.create_kai_template_from(_template_form(_body(request)), request.user)
        if template is None:
            raise BadRequest(' '.join(notes.errors) or 'Please fill in all required fields.', code='invalid_input')
        return Response({'template': _template_json(template), 'message': notes[0][1]}, status=201)


class KaiTemplateView(_TemplatesView):
    web_page = 'edit_kai_template'

    def get(self, request, template_id):
        self.gate(request)
        return Response({'template': _template_json(get_object_or_404(KaiReportTemplate, id=template_id)),
                         **self.choices()})

    def post(self, request, template_id):
        self.gate(request)
        template = get_object_or_404(KaiReportTemplate, id=template_id)
        notes = web.update_kai_template_from(template, _template_form(_body(request)))
        if notes.errors:
            raise BadRequest(' '.join(notes.errors), code='invalid_input')
        template.refresh_from_db()
        return Response({'template': _template_json(template), 'message': notes[0][1]})


class KaiTemplateDeleteView(_TemplatesView):
    web_page = 'delete_kai_template'

    def post(self, request, template_id):
        self.gate(request)
        message = web.delete_kai_template_row(get_object_or_404(KaiReportTemplate, id=template_id))
        return Response({'message': message})


# ── Member grants (manage_kai_permissions) ───────────────────────────────────

def _grants_committee(request, code):
    committee = get_object_or_404(Committee, code=code, is_kai_committee=True)
    if not web._is_kai_chair(request.user, committee):
        raise exceptions.PermissionDenied('Only Kai chairs can manage member permissions.', code='forbidden')
    return committee


def _grant_row(row):
    member = row['member']
    return {'user_id': member.pk, 'name': member.name, 'member_type': member.member_type,
            'is_chair': row['is_chair'], **{f: bool(row[f]) for f in web_perms.KAI_PERM_FIELDS}}


#: The page's column labels.
PERM_LABELS = {
    'can_view_report_list': 'View List',
    'can_view_report_details': 'View Details',
    'can_view_submitter_identity': 'Submitter ID',
    'can_view_accused_identity': 'Accused ID',
    'can_edit_open_cases': 'Edit Cases',
    'can_add_activity': 'Add Activity',
    'can_close_cases': 'Close Cases',
}
#: The page's column tooltips.
PERM_HINTS = {
    'can_view_report_list': 'Can see the report list',
    'can_view_report_details': 'Can open individual reports',
    'can_view_submitter_identity': 'Can see submitter names',
    'can_view_accused_identity': 'Can see accused names',
    'can_edit_open_cases': 'Can update case status/deliberation',
    'can_add_activity': 'Can add notes/activity',
    'can_close_cases': 'Can archive/close cases',
}


class KaiPermissionsView(MobileAPIView):
    web_page = 'manage_kai_permissions'
    web_page_toggle = False   # @login_required only

    def get(self, request, code):
        committee = _grants_committee(request, code)
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'members': [_grant_row(r) for r in web_perms.kai_permission_rows(committee)],
            'fields': [{'key': f, 'label': PERM_LABELS[f], 'hint': PERM_HINTS[f]} for f in web_perms.KAI_PERM_FIELDS],
            'links': {'page': reverse('manage_kai_permissions', args=[committee.code])},
        })


class KaiPermissionUpdateView(MobileAPIView):
    web_page = 'update_kai_member_permission'
    web_page_toggle = False

    def post(self, request, code, user_id):
        committee = _grants_committee(request, code)
        member = get_object_or_404(ParliamentUser, user_id=user_id)
        if web._is_kai_chair(member, committee):
            raise BadRequest('Chairs always have full access; no explicit permission needed.',
                             code='invalid_input')
        body = _body(request)
        flags = {}
        for field in web_perms.KAI_PERM_FIELDS:
            value = body.get(field, False)
            if not isinstance(value, bool):
                raise BadRequest(f'{PERM_LABELS[field]} must be true or false.', code='invalid_input')
            flags[field] = value
        perm = web_perms.set_kai_member_permission(committee, member, flags, request.user)
        return Response({'member': {'user_id': member.pk, 'name': member.name, 'member_type': member.member_type,
                                    'is_chair': False,
                                    **{f: bool(getattr(perm, f)) for f in web_perms.KAI_PERM_FIELDS}}})


class KaiPermissionsResetView(MobileAPIView):
    web_page = 'reset_kai_permissions'
    web_page_toggle = False

    def post(self, request, code):
        committee = _grants_committee(request, code)
        deleted = web_perms.reset_kai_member_permissions(committee)
        return Response({'deleted': deleted,
                         'message': f'Reset {deleted} member permission(s) to default (no access).'})
