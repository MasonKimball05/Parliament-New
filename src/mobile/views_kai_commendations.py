"""
Kai commendations management in the app (v4.x): the committee's side of the
website's commendation pages (``manage_kai_commendations`` /
``manage_kai_commendation_detail`` in src/view/kai_commendations.py), behind
the same gates: the ``kai_reports`` feature flag and the Kai committee's own
access (``_get_kai_access``: its chairs, a ``KaiMemberPermission`` grant, or an
admin's live break-glass; being an admin or an officer alone is not enough).

* ``GET officer/kai/commendations/?status=`` — the list (can_view_report_list):
  newest first, at most ``KAI_COMMENDATION_LIST_LIMIT``, with ``truncated``.
* ``GET officer/kai/commendations/<id>/`` — one commendation
  (can_view_report_details): the description, custom answers, committee
  notes, activity and review, plus what this reviewer may change.
* ``POST officer/kai/commendations/<id>/`` ``{action: update_status, status}``,
  ``{action: assign, assigned_to}`` (can_edit_open_cases) or
  ``{action: update_notes, committee_notes}`` (can_add_activity), through the
  page's own ``update_commendation``, so the activity rows are the page's.

What a reviewer sees is exactly what the pages show them: a commendation has
no accused party, so the rows aren't redacted per field (see the list
template's v3.28.9 note). The submitter is named, with the anonymity flag, as
on the pages. The attachment and uploaded answers are site paths, streamed by
the website's own Kai-gated file views. Nothing here is cached on the phone.
"""
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.text import Truncator
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import Committee, KaiCommendation
from src.view import kai_commendations as web

NOTES_MAX = 20000


def _iso(value):
    return value.isoformat() if value else None


def kai_commendation_access(user):
    """``_get_kai_access`` for the Kai committee, or None when Kai is off or has no committee."""
    from src.view.kai_reports import _get_kai_access
    if not check_feature_enabled('kai_reports'):
        return None
    committee = Committee.objects.filter(is_kai_committee=True).first()
    if committee is None:
        return None
    return _get_kai_access(user, committee)


class _KaiCommendationView(MobileAPIView):
    web_page_toggle = False   # the Kai pages have @require_feature_flag, not a page toggle
    #: The access flag this endpoint needs (the page's own check).
    needs = 'can_view_report_list'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_feature_enabled('kai_reports'):
            raise exceptions.PermissionDenied('Kai is turned off.', code='feature_disabled')
        access = kai_commendation_access(request.user)
        if access is None:
            raise exceptions.NotFound('Kai committee not found.')
        if not access[self.needs]:
            raise exceptions.PermissionDenied('You do not have permission to view Kai commendations.',
                                              code='forbidden')
        self.access = access


def _person(u):
    return {'user_id': u.user_id, 'name': u.name} if u is not None else None


def _row_json(c):
    """A list row, as manage_commendations.html shows it."""
    return {
        'id': c.id,
        'number': c.display_number,
        'title': c.title,
        'description': Truncator(c.description or '').words(30),
        'commended_member': _person(c.commended_member),
        'assigned_to': _person(c.assigned_to),
        'status': c.status,
        'status_label': c.get_status_display(),
        'is_submitter_anonymous': c.is_submitter_anonymous,
        'submitted_by': _person(c.submitted_by),
        'submitted_at': _iso(c.submitted_at),
        'has_attachment': bool(c.attachment),
    }


def _response_json(r):
    """A custom answer, as commendation_detail.html shows it."""
    field_type = r.field.field_type
    file = (reverse('kai_commendation_response_file', args=[r.id])
            if field_type == 'file' and r.file_value else None)
    if file:
        value = None
    elif field_type in ('checkbox', 'multiselect'):
        value = ', '.join(str(v) for v in (r.json_value or []))
    else:
        shown = r.get_display_value()
        value = '' if shown is None else str(shown)
    return {'label': r.field.label, 'field_type': field_type, 'value': value, 'file': file}


def _statuses():
    return [{'value': v, 'label': label} for v, label in KaiCommendation.STATUS_CHOICES]


class KaiCommendationListView(_KaiCommendationView):
    web_page = 'manage_kai_commendations'

    def get(self, request):
        status_filter = request.query_params.get('status', '')
        rows, truncated = web.commendation_list(status_filter)
        return Response({
            'commendations': [_row_json(c) for c in rows],
            'truncated': truncated,
            'status': status_filter,
            'statuses': _statuses(),
            'web_path': reverse('manage_kai_commendations'),
        })


class KaiCommendationView(_KaiCommendationView):
    web_page = 'manage_kai_commendation_detail'
    needs = 'can_view_report_details'

    def _detail(self, commendation):
        c = commendation
        can_edit = bool(self.access['can_edit_open_cases'])
        return Response({
            'commendation': {
                **_row_json(c),
                'description': c.description,
                'attachment': reverse('kai_commendation_attachment', args=[c.id]) if c.attachment else None,
                'committee_notes': c.committee_notes or '',
                'reviewed_at': _iso(c.reviewed_at),
                'reviewed_by': c.reviewed_by.name if c.reviewed_by_id else None,
            },
            'responses': [_response_json(r) for r in c.custom_responses.select_related('field').all()],
            'activity': [{
                'action': a.action,
                'label': a.get_action_display(),
                'details': a.details or '',
                'user': a.user.name if a.user_id else None,
                'timestamp': _iso(a.timestamp),
            } for a in c.activity_log.select_related('user').all()],
            'can': {'edit': can_edit, 'notes': bool(self.access['can_add_activity'])},
            'statuses': _statuses(),
            # The Assignment picker's choices, only when the page shows the picker.
            'members': ([{'user_id': m.user_id, 'name': m.name}
                         for m in web.assignable_members().only('user_id', 'name')]
                        if can_edit else []),
            'web_path': reverse('manage_kai_commendation_detail', args=[c.id]),
        })

    def get(self, request, commendation_id):
        return self._detail(get_object_or_404(KaiCommendation, id=commendation_id))

    def post(self, request, commendation_id):
        from django.core.exceptions import PermissionDenied
        commendation = get_object_or_404(KaiCommendation, id=commendation_id)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action')
        data = {
            'status': str(body.get('status') or ''),
            'assigned_to': str(body.get('assigned_to') or ''),
            'committee_notes': str(body.get('committee_notes') or '')[:NOTES_MAX],
        }
        try:
            done = web.update_commendation(commendation, request.user, self.access, action, data)
        except PermissionDenied:
            raise exceptions.PermissionDenied('You do not have permission to make this change.', code='forbidden')
        if not done:
            raise BadRequest('That isn’t a status.', code='invalid_input')
        commendation.refresh_from_db()
        return Response({**self._detail(commendation).data, 'message': done})
