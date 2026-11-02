"""
Service hours review in the app: the website's VPP pages
(``src/view/service_hours.py``) for the officer who approves hours on a phone.
The Vice President of Programming and admins only, exactly ``vpp_required``
(``user_is_vpp``).

* ``GET officer/service-hours/?period=`` — the dashboard (``service_dashboard``):
  the period's totals, every active member's progress (lowest first) and the
  ten newest submissions.
* ``GET officer/service-hours/submissions/?period=&status=&member=&page=`` —
  the list (``view_service_submissions``), 50 a page, with each multi-date
  submission's batch counts.
* ``POST officer/service-hours/submissions/bulk/`` ``{action: approve|reject,
  ids}`` — ``bulk_actions_service``; only still-pending rows change.
* ``GET/POST officer/service-hours/submissions/<id>/`` — one submission
  (``manage_service_submission``) and ``{action: approve|reject, notes}``. A
  rejection needs notes, as on the page.

Every number and every write goes through the same functions the web views
call, so the activity log rows are the website's. Periods, expectations and
service events are native since batch 5 (``views_service_admin.py``); hour
adjustments, the form builder and the CSV export stay on the website. The
responses carry the website's paths for all of them (``web``), which builds
from before batch 5 still open.
"""
from src.app_review import exclude_review_accounts
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_directory import photo_path
from src.mobile.views_excuses_polls import BadRequest
from src.models import ParliamentUser, ServicePeriod, ServiceHoursSubmission
from src.permissions import user_is_vpp
from src.view import service_hours as web

PAGE_SIZE = 50          # the web list's Paginator
BULK_MAX = 500
ACTIONS = ('approve', 'reject')


def _num(value):
    return float(value) if value is not None else None


def _iso(value):
    return value.isoformat() if value else None


def _id(raw, what):
    """A query/body id as an int, or None when absent. The web page 500s on junk; the app gets a 400."""
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise BadRequest(f'That {what} isn’t valid.', code='invalid_input')


def _period_json(p):
    return {
        'id': p.id,
        'name': p.name,
        'start_date': p.start_date.isoformat(),
        'end_date': p.end_date.isoformat(),
        'is_active': p.is_active,
    }


def _member_json(m):
    return {'user_id': m.user_id, 'name': m.get_display_name(), 'photo': photo_path(m)}


def _submission_json(s):
    out = {
        'id': s.id,
        'member': _member_json(s.submitted_by),
        'hours': _num(s.hours),
        'service_date': s.service_date.isoformat(),
        'organization': s.organization,
        'description': s.description,
        'period': {'id': s.period_id, 'name': s.period.name},
        'status': s.status,
        'status_label': s.get_status_display(),
        'submitted_at': _iso(s.submitted_at),
        'reviewed_by': s.reviewed_by.get_display_name() if s.reviewed_by_id else None,
        'reviewed_at': _iso(s.reviewed_at),
        'reviewer_notes': s.reviewer_notes or '',
        'batch_id': str(s.batch_id) if s.batch_id else None,
    }
    if hasattr(s, 'batch_total_count'):
        out['batch_pending_count'] = s.batch_pending_count
        out['batch_total_count'] = s.batch_total_count
    return out


def _response_json(r):
    """A custom field answer as manage_submission.html shows it."""
    field_type = r.field.field_type
    file = (reverse('service_hours_response_file', args=[r.id])
            if field_type == 'file' and r.file_value else None)
    if file:
        value = None
    elif field_type in ('multiselect', 'checkbox'):
        value = ', '.join(str(v) for v in (r.json_value or []))
    elif r.number_value:
        value = str(r.number_value.normalize())
    else:
        value = r.text_value or ''
    return {'label': r.field.label, 'field_type': field_type, 'value': value, 'file': file}


def _web_links(period=None):
    return {
        'dashboard': reverse('service_dashboard'),
        'submissions': reverse('view_service_submissions'),
        'periods': reverse('manage_service_periods'),
        'events': reverse('service_events_list'),
        'form_builder': reverse('service_form_builder'),
        'export': reverse('export_service_csv') + (f'?period={period.id}' if period else ''),
        'expectations': reverse('manage_member_expectations', args=[period.id]) if period else None,
    }


class _ServiceReviewView(MobileAPIView):
    web_page_toggle = False   # the service hours officer pages have no page toggle

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_vpp(request.user):
            raise exceptions.PermissionDenied(
                'Only the Vice President of Programming can review service hours.', code='forbidden')


class ServiceDashboardView(_ServiceReviewView):
    web_page = 'service_dashboard'

    def get(self, request):
        period, periods = web.resolve_dashboard_period(_id(request.query_params.get('period'), 'period'))
        stats, progress, recent = web.dashboard_data(period)
        return Response({
            'period': _period_json(period) if period else None,
            'default_hours_required': _num(period.default_hours_required) if period else None,
            'periods': [_period_json(p) for p in periods],
            'stats': {
                'total_submissions': stats['total_submissions'],
                'pending_count': stats['pending_count'],
                'approved_count': stats['approved_count'],
                'rejected_count': stats['rejected_count'],
                'total_approved_hours': _num(stats['total_approved_hours']),
                'total_pending_hours': _num(stats['total_pending_hours']),
            } if stats else None,
            'members': [{
                **_member_json(row['member']),
                'member_type': row['member'].member_type,
                'approved_hours': _num(row['approved_hours']),
                'submitted_hours': _num(row['submitted_hours']),
                'adjusted_hours': _num(row['adjusted_hours']),
                'pending_hours': _num(row['pending_hours']),
                'expected_hours': _num(row['expected_hours']),
                'progress_percent': row['progress_percent'],
                'completed': row['completed'],
            } for row in progress],
            'recent': [_submission_json(s) for s in recent],
            'web': _web_links(period),
        })


class ServiceSubmissionListView(_ServiceReviewView):
    web_page = 'view_service_submissions'

    def get(self, request):
        qp = request.query_params
        period_id = _id(qp.get('period'), 'period')
        status = qp.get('status') or None
        member_id = qp.get('member') or None
        page = Paginator(web.filtered_submissions(period_id, status, member_id), PAGE_SIZE).get_page(qp.get('page'))
        rows = web.attach_batch_counts(list(page.object_list))
        members = exclude_review_accounts(
            ParliamentUser.objects.filter(member_status='Active')).order_by('name').only('user_id', 'name')
        return Response({
            'submissions': [_submission_json(s) for s in rows],
            'page': page.number,
            'pages': page.paginator.num_pages,
            'count': page.paginator.count,
            'filters': {'period': period_id, 'status': status, 'member': member_id},
            'periods': [_period_json(p) for p in ServicePeriod.objects.all().order_by('-start_date')],
            'members': [{'user_id': m.user_id, 'name': m.name} for m in members],
            'statuses': [{'value': v, 'label': label} for v, label in ServiceHoursSubmission.STATUS_CHOICES
                         if v != 'draft'],
            'web': _web_links(),
        })


class ServiceSubmissionBulkView(_ServiceReviewView):
    web_page = 'bulk_actions_service'

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action')
        if action not in ACTIONS:
            raise BadRequest('Approve or reject.', code='invalid_input')
        ids = body.get('ids')
        if not isinstance(ids, list) or not ids:
            raise BadRequest('No submissions selected.', code='invalid_input')
        if len(ids) > BULK_MAX:
            raise BadRequest(f'At most {BULK_MAX} at once.', code='invalid_input')
        clean = [_id(i, 'submission') for i in ids]
        count = web.bulk_review_submissions(clean, request.user, action)
        return Response({'action': action, 'count': count})


class ServiceSubmissionView(_ServiceReviewView):
    web_page = 'manage_service_submission'

    def _response(self, submission):
        activity, responses = web.submission_detail_data(submission)
        out = _submission_json(submission)
        out['member']['member_type'] = submission.submitted_by.member_type
        out['attachment'] = (reverse('service_hours_attachment', args=[submission.id])
                             if submission.attachment else None)
        return Response({
            'submission': out,
            'responses': [_response_json(r) for r in responses],
            'activity': [{
                'action': a.action,
                'label': a.get_action_display(),
                'user': a.user.get_display_name() if a.user_id else None,
                'details': a.details or '',
                'timestamp': _iso(a.timestamp),
            } for a in activity],
            'can_review': submission.status == 'pending',
            'web_path': reverse('manage_service_submission', args=[submission.id]),
        })

    def get(self, request, submission_id):
        return self._response(get_object_or_404(web.submission_queryset(), id=submission_id))

    def post(self, request, submission_id):
        submission = get_object_or_404(web.submission_queryset(), id=submission_id)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action')
        notes = str(body.get('notes') or '').strip()
        if action not in ACTIONS:
            raise BadRequest('Approve or reject.', code='invalid_input')
        # The page only offers the review form while a submission is pending.
        if submission.status != 'pending':
            raise BadRequest(f'This submission is already {submission.get_status_display().lower()}.',
                             code='not_pending')
        error = web.review_submission(submission, request.user, action, notes)
        if error:
            raise BadRequest(error, code='notes_required')
        return self._response(get_object_or_404(web.submission_queryset(), id=submission_id))
