"""
My Kai reports in the app (v4.x): the website's member Kai dashboard
(``user_kai_dashboard``, /kai/) and its per-case page (``user_view_kai_report``,
/kai/my-report/<id>/), read-only, through the same helpers as the website
(``member_dashboard`` / ``member_report_context`` in
src/view/kai_user_dashboard.py), so the app lists exactly what the pages do.

* ``GET kai/`` — the cases the member filed (with "Regarding: <accused>", as
  the page shows the submitter), the cases they're named in once the committee
  has addressed them (no submitter, ever: the page never shows one), the cases
  they stand in on, how many of their requests are pending, and the site paths
  for Submit Report and (with a review grant) the Review Dashboard.
* ``GET kai/reports/<id>/`` — one case as its submitter or the accused sees
  it: description, category, tags, the accused's name (the page shows it to
  both parties), the custom form answers, closure / drop requests, appeals and
  the appeal window, and the activity feed as action + time only (never its
  ``details`` or author, and never the chair / committee notes rows).

Who sees what is the pages' rule. One deliberate tightening: the website's
case page fetches by ``submitted_by=user | targeted_to=user`` and so would show
the accused a case the dashboard is still hiding from them (outcome
``pending``) if they had its URL. The app 404s that case for the accused,
matching the dashboard, which is the only way in.

Filing a report, requesting closure, dropping a case, filing an appeal and
opening a stand-in case (the committee's manage page) all stay on the website;
each comes back as a site path (``links`` / ``actions``). The attachment and
uploaded answers are site paths too: ``serve_private_upload`` streams them to
the two parties only.

Neither web page has ``@require_page_enabled``, so only the pledge restriction
applies, as it does there.
"""
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import KaiReport
from src.view.kai_user_dashboard import member_dashboard, member_report_context


def _iso(dt):
    return dt.isoformat() if dt else None


def _report_row(report, regarding=None):
    row = {
        'id': report.id,
        'title': report.title,
        'category': report.category,
        'category_label': report.get_category_display(),
        'status': report.status,
        'status_label': report.get_status_display(),
        'outcome': report.deliberation_outcome,
        'outcome_label': report.get_deliberation_outcome_display(),
        'submitted_at': _iso(report.submitted_at),
    }
    if regarding is not None:
        row['regarding'] = regarding
    return row


class KaiDashboardView(MobileAPIView):
    web_page = 'user_kai_dashboard'
    web_page_toggle = False   # @login_required only

    def get(self, request):
        ctx = member_dashboard(request.user)
        submitted = [
            # "Regarding: <accused>" only when the case names someone, as on the page.
            _report_row(r, regarding=r.accused_display_name if r.targeted_to_id else '')
            for r in ctx['submitted_reports']
        ]
        # Cases the member is named in: never the submitter (the page shows none).
        named = [_report_row(r) for r in ctx['accused_reports']]
        standing_in = [{
            'report_id': rec.report_id,
            'case_number': rec.report.display_number,
            'title': rec.report.title,
            'standing_in_for': rec.user.name,
            'appointed_at': _iso(rec.appointed_at),
            'path': reverse('manage_kai_report', args=[rec.report_id]),
        } for rec in ctx['standin_recusals']]
        return Response({
            'submitted': submitted,
            'named': named,
            'standing_in': standing_in,
            'pending_requests': len(ctx['pending_closures']),
            'can_view_kai_reports': bool(ctx['can_view_kai_reports']),
            'links': {
                'page': reverse('user_kai_dashboard'),
                'submit': reverse('submit_kai_report'),
                'review': reverse('view_kai_reports') if ctx['can_view_kai_reports'] else None,
            },
        })


def _response_value(resp):
    """One custom answer as the case page renders it: (text, file path)."""
    ftype = resp.field.field_type
    if ftype == 'file' and resp.file_value:
        return None, reverse('kai_response_file', args=[resp.id])
    if ftype in ('checkbox', 'multiselect'):
        values = resp.json_value or []
        if not isinstance(values, (list, tuple)):
            values = [values]
        return ', '.join(str(v) for v in values), None
    value = resp.get_display_value()
    return (str(value) if value not in (None, '') else 'Not provided'), None


class KaiReportView(MobileAPIView):
    web_page = 'user_view_kai_report'
    web_page_toggle = False   # @login_required only

    def get(self, request, report_id):
        user = request.user
        report = (KaiReport.objects
                  .filter(Q(submitted_by=user) | Q(targeted_to=user), id=report_id)
                  .first())
        if report is None:
            raise Http404
        # The dashboard's rule for the accused: nothing until it's addressed.
        if report.submitted_by_id != user.pk and report.deliberation_outcome == 'pending':
            raise Http404

        ctx = member_report_context(report, user)
        is_accused = ctx['is_accused']
        page = reverse('user_view_kai_report', args=[report.id])

        responses = []
        for resp in ctx['custom_responses']:
            text, file_path = _response_value(resp)
            responses.append({'label': resp.field.label, 'value': text, 'file': file_path})

        appeal = None
        if is_accused:
            appeal = {
                'can_appeal': ctx['can_appeal'],
                'days_remaining': ctx['appeal_days_remaining'],
                'closes_at': _iso(ctx['appeal_window_closes_at']),
                'window_days': ctx['appeal_window_days'],
                # Shown only once notice was given, as the page does.
                'blocked_reason': (ctx['appeal_blocked_reason']
                                   if not ctx['can_appeal'] and report.accused_notified_at else ''),
                # The form is on the case page.
                'path': page if ctx['can_appeal'] else None,
            }

        return Response({
            'id': report.id,
            'title': report.title,
            'is_submitter': ctx['is_submitter'],
            'is_accused': is_accused,
            'status': report.status,
            'status_label': report.get_status_display(),
            'category': report.category,
            'category_label': report.get_category_display(),
            'description': report.description,
            'directed_to': report.accused_display_name if report.targeted_to_id else '',
            'attachment': (reverse('kai_report_attachment', args=[report.id])
                           if report.attachment else None),
            'tags': report.get_tag_labels(),
            'outcome': report.deliberation_outcome,
            'outcome_label': report.get_deliberation_outcome_display(),
            'submitted_at': _iso(report.submitted_at),
            'reviewed_at': _iso(report.reviewed_at),
            'responses': responses,
            'requests': [{
                'type': cr.request_type,
                'type_label': 'Drop Request' if cr.request_type == 'drop' else 'Closure Request',
                'status': cr.status,
                'status_label': cr.get_status_display(),
                'requested_at': _iso(cr.requested_at),
                'reason': cr.reason,
                'review_notes': cr.review_notes,
            } for cr in ctx['closure_requests']],
            'appeals': [{
                'level_label': a.get_level_display(),
                'status': a.status,
                'status_label': a.get_status_display(),
                'filed_at': _iso(a.filed_at),
                'outcome_notes': a.outcome_notes,
            } for a in ctx['appeals']],
            'appeal': appeal,
            # Action + time only: `details` and the author name the parties.
            'activity': [{
                'action': act.action,
                'label': act.get_action_display(),
                'timestamp': _iso(act.timestamp),
            } for act in ctx['activity_log']],
            'actions': {
                'request_closure': (reverse('kai_request_closure', args=[report.id])
                                    if ctx['can_request_closure'] else None),
                'drop_case': (reverse('kai_request_drop', args=[report.id])
                              if ctx['can_drop_case'] else None),
            },
            'links': {'page': page},
        })
