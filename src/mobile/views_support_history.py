"""
My bug reports, My submissions and the Ideas board in the app (v4.4.0): the
website's ``my_bug_reports`` (/bug-report/my-reports/),
``my_feedback_requests`` (/feedback/my-submissions/) and ``feedback_tracker``
(/feedback/ideas/), through the same functions as the web views
(``own_bug_reports`` in src/view/bug_report.py; ``own_feedback_requests``,
``feature_ideas``, ``feature_idea_counts`` and ``can_manage_feedback`` in
src/view/feedback.py), so the app lists exactly what the pages do.

* ``GET support/bug-reports/`` — the member's own bug reports, newest first:
  issue type, page, priority, status and when, as the page's table shows them,
  plus whether a screenshot is attached and the path of the bug tracker's
  detail page (/bug-tracker/<id>/, where the screenshot shows).
* ``GET support/feedback/mine/`` — the member's own ideas and support tickets
  together, newest first: kind, title, page, status and when.
* ``GET support/feedback/ideas/?status=&priority=`` — the public board:
  feature ideas only (never support tickets), in the page's order (in
  progress, acknowledged, new, resolved, won't do, duplicate), with the four
  summary counts, the filter choices and who posted each. A filter value that
  isn't one of the choices is ignored.

v4.5.0 — the bug tracker, each bug report's and submission's detail page and
the two admin pages, natively, through the web views' own helpers
(``tracked_bug_reports``, ``bug_report_counts``, ``bug_report_for_detail``,
``update_bug_report``, ``screenshot_visible_to``; ``feedback_for_detail``,
``feedback_visible_to``, ``admin_feedback_requests``, ``feedback_admin_counts``,
``update_feedback``):

* ``GET support/bug-tracker/?status=&type=`` — ``bug_tracker``: every report
  in the page's order, its counts, filters and ``can_manage``.
* ``GET support/bug-tracker/<id>/`` — ``bug_report_detail``: what the page
  shows any member (the report, its steps, where it happened, the device),
  plus the admin notes for the bug admin only, as the page. ``screenshot`` is
  the API path of the image for the reporter and the admin, the only ones
  ``serve_bug_report_screenshot`` lets see it; null for everyone else.
* ``GET support/bug-tracker/<id>/screenshot/`` — the image (same rule).
* ``GET support/bug-tracker/admin/?status=&type=&priority=&device_type=`` —
  ``bug_admin`` (the bug admin, user_id 73, only).
* ``POST support/bug-tracker/<id>/update/`` ``{status, admin_notes}`` —
  ``bug_admin_update``: a blank or unknown status keeps the status, blank
  notes keep the notes; resolving stamps when and by whom. Answers with the
  detail.
* ``GET support/feedback/<id>/`` — ``feedback_request_detail``: an idea to
  any member, a ticket to its submitter and the admin only (404 otherwise);
  admin notes to the admin only. ``attachment`` is its API path, when it has
  one, under the same rule (``serve_feedback_attachment``).
* ``GET support/feedback/<id>/attachment/`` — the file.
* ``GET support/feedback/admin/?status=&type=&priority=`` — ``feedback_admin``
  (the feedback admin, user_id 73, only): ideas and tickets with their counts.
* ``POST support/feedback/<id>/update/`` ``{status, admin_notes}`` —
  ``feedback_admin_update``, as bug reports.

A filter value that isn't one of the choices is ignored, and so is (on the
updates) a status that isn't one, as the web does. No votes or comments: the
pages have none.

Who sees what is each page's rule: a member's own reports and submissions;
the board shows every member's ideas. Staff fields (admin notes, the
submitter's browser info on the lists) stay out of the lists, as on the
pages.

Every signed-in member (``@login_required`` only, no page toggle), with the
pledge page restrictions of each page; the admin pages need user_id 73
(``bug_admin_required`` / ``feedback_admin_required``).
"""
import os

from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import BugReport, FeedbackRequest
from src.view.bug_report import (
    bug_device_type_choices, bug_report_counts, bug_report_for_detail, can_manage_bugs, own_bug_reports,
    screenshot_visible_to, tracked_bug_reports, update_bug_report,
)
from src.view.feedback import (
    admin_feedback_requests, can_manage_feedback, feature_idea_counts, feature_ideas, feedback_admin_counts,
    feedback_for_detail, feedback_visible_to, own_feedback_requests, update_feedback,
)
from src.view.serve_private_upload import _stream_private_file


def _iso(dt):
    return dt.isoformat() if dt else None


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _choice(value, pairs):
    """``value`` if it's one of ``pairs``, else '' (no filter)."""
    value = str(value or '')
    return value if value in dict(pairs) else ''


class MyBugReportsView(MobileAPIView):
    web_page = 'my_bug_reports'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        return Response({
            'reports': [{
                'id': r.id,
                'description': r.description,
                'issue_type': r.issue_type,
                'issue_type_label': r.get_issue_type_display(),
                'page': r.page,
                'page_label': r.get_page_display() if r.page else '',
                'priority': r.priority,
                'priority_label': r.get_priority_display(),
                'status': r.status,
                'status_label': r.get_status_display(),
                'submitted_at': _iso(r.submitted_at),
                'has_screenshot': bool(r.screenshot),
                # The tracker's detail page (any member can read it), which
                # shows the screenshot; the my-reports table doesn't link it.
                'links': {'detail': reverse('bug_report_detail', args=[r.id])},
            } for r in own_bug_reports(request.user)],
            'links': {'page': reverse('my_bug_reports'), 'new': reverse('bug_report')},
        })


class MyFeedbackView(MobileAPIView):
    web_page = 'my_feedback_requests'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        return Response({
            'submissions': [{
                'id': f.id,
                'request_type': f.request_type,
                'request_type_label': f.get_request_type_display(),
                'title': f.title,
                'description': f.description,
                'page': f.page,
                'page_label': f.get_page_display() if f.page else '',
                'status': f.status,
                'status_label': f.get_status_display(),
                'submitted_at': _iso(f.submitted_at),
                'links': {'detail': reverse('feedback_request_detail', args=[f.id])},
            } for f in own_feedback_requests(request.user)],
            'links': {'page': reverse('my_feedback_requests'), 'new': reverse('feedback_request')},
        })


class FeedbackIdeasView(MobileAPIView):
    web_page = 'feedback_tracker'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        params = request.query_params
        status = _choice(params.get('status'), FeedbackRequest.STATUS_CHOICES)
        priority = _choice(params.get('priority'), FeedbackRequest.PRIORITY_CHOICES)
        counts = feature_idea_counts()
        can_manage = can_manage_feedback(request.user)
        return Response({
            'status': status,
            'priority': priority,
            'counts': {
                'total': counts['total_count'],
                'new': counts['new_count'],
                'in_progress': counts['in_progress_count'],
                'resolved': counts['resolved_count'],
            },
            'status_choices': _choices(FeedbackRequest.STATUS_CHOICES),
            'priority_choices': _choices(FeedbackRequest.PRIORITY_CHOICES),
            'ideas': [{
                'id': f.id,
                'title': f.title,
                'description': f.description,
                'page': f.page,
                'page_label': f.get_page_display() if f.page else '',
                'priority': f.priority,
                'priority_label': f.get_priority_display(),
                'status': f.status,
                'status_label': f.get_status_display(),
                # The page's `submitted_by.name|default:"Anonymous"`.
                'submitted_by': (f.submitted_by.name if f.submitted_by_id else '') or 'Anonymous',
                'is_mine': f.submitted_by_id == request.user.pk,
                'submitted_at': _iso(f.submitted_at),
                'links': {'detail': reverse('feedback_request_detail', args=[f.id])},
            } for f in feature_ideas(status, priority)],
            'can_manage': can_manage,
            'links': {
                'page': reverse('feedback_tracker'),
                'new': reverse('feedback_request'),
                'admin': reverse('feedback_admin') if can_manage else None,
            },
        })



# ── v4.5.0: the bug tracker, detail pages and admin pages ────────────────────

def _name(member):
    """The pages' ``name|default:"Anonymous"``."""
    return (member.name if member else '') or 'Anonymous'


def _counts(c):
    return {'total': c['total_count'], 'new': c['new_count'],
            'in_progress': c['in_progress_count'], 'resolved': c['resolved_count']}


def _bug_row(r, user):
    return {
        'id': r.id,
        'description': r.description,
        'issue_type': r.issue_type,
        'issue_type_label': r.get_issue_type_display(),
        'page': r.page,
        'page_label': r.get_page_display() if r.page else '',
        'priority': r.priority,
        'priority_label': r.get_priority_display(),
        'status': r.status,
        'status_label': r.get_status_display(),
        'submitted_by': _name(r.submitted_by),
        'is_mine': r.submitted_by_id == user.pk,
        'submitted_at': _iso(r.submitted_at),
        'has_screenshot': bool(r.screenshot),
        'links': {'detail': reverse('bug_report_detail', args=[r.id])},
    }


def _require_bug_admin(user):
    if not can_manage_bugs(user):
        raise exceptions.PermissionDenied('Only the bug admin can do that.', code='forbidden')


def _require_feedback_admin(user):
    if not can_manage_feedback(user):
        raise exceptions.PermissionDenied('Only the feedback admin can do that.', code='forbidden')


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _text(value):
    return '' if value is None else str(value)


def _file(fieldfile):
    """
    The upload itself, for the app (``/media/`` wants a website session).
    The web's own streamer: MEDIA_ROOT only, safe disposition, 404 when the
    file is gone. The caller has already checked who may see it.
    """
    response = _stream_private_file(fieldfile)
    response['Cache-Control'] = 'private, no-store'
    return response


class BugTrackerView(MobileAPIView):
    web_page = 'bug_tracker'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        params = request.query_params
        status = _choice(params.get('status'), BugReport.STATUS_CHOICES)
        issue_type = _choice(params.get('type'), BugReport.ISSUE_TYPES)
        can_manage = can_manage_bugs(request.user)
        return Response({
            'status': status,
            'type': issue_type,
            'counts': _counts(bug_report_counts()),
            'status_choices': _choices(BugReport.STATUS_CHOICES),
            'issue_types': _choices(BugReport.ISSUE_TYPES),
            'reports': [_bug_row(r, request.user) for r in tracked_bug_reports(status, issue_type)],
            'can_manage': can_manage,
            'links': {
                'page': reverse('bug_tracker'),
                'new': reverse('bug_report'),
                'admin': reverse('bug_admin') if can_manage else None,
            },
        })


def _bug_detail(report, user):
    can_manage = can_manage_bugs(user)
    return {
        'report': {
            **_bug_row(report, user),
            'feature': report.feature,
            'page_url': report.page_url,
            'steps_to_reproduce': report.steps_to_reproduce,
            'expected_behavior': report.expected_behavior,
            'actual_behavior': report.actual_behavior,
            # The page shows the device and the raw browser info to everyone.
            'device_type': report.device_type,
            'browser': report.browser,
            'operating_system': report.operating_system,
            'browser_info': report.browser_info,
            'resolved_at': _iso(report.resolved_at),
            'resolved_by': report.resolved_by.name if report.resolved_by_id else None,
            # The page's `{% if bug_report.admin_notes and can_manage %}`.
            'admin_notes': report.admin_notes if can_manage else None,
            'screenshot': (f'support/bug-tracker/{report.id}/screenshot/'
                           if report.screenshot and screenshot_visible_to(report, user) else None),
            'screenshot_name': (os.path.basename(report.screenshot.name)
                                if report.screenshot and screenshot_visible_to(report, user) else None),
        },
        'can_manage': can_manage,
        'status_choices': _choices(BugReport.STATUS_CHOICES),
        'links': {'page': reverse('bug_report_detail', args=[report.id]), 'tracker': reverse('bug_tracker')},
    }


class BugReportDetailView(MobileAPIView):
    web_page = 'bug_report_detail'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request, bug_id):
        report = bug_report_for_detail(bug_id)
        if report is None:
            raise exceptions.NotFound()
        return Response(_bug_detail(report, request.user))


class BugReportScreenshotView(MobileAPIView):
    web_page = 'bug_report_screenshot'
    web_page_toggle = False   # the web view has no @require_page_enabled

    def get(self, request, bug_id):
        report = BugReport.objects.filter(id=bug_id).first()
        if report is None or not screenshot_visible_to(report, request.user):
            raise exceptions.NotFound()
        return _file(report.screenshot)


class BugAdminView(MobileAPIView):
    web_page = 'bug_admin'
    web_page_toggle = False   # @bug_admin_required only

    def get(self, request):
        _require_bug_admin(request.user)
        params = request.query_params
        status = _choice(params.get('status'), BugReport.STATUS_CHOICES)
        issue_type = _choice(params.get('type'), BugReport.ISSUE_TYPES)
        priority = _choice(params.get('priority'), BugReport.PRIORITY_CHOICES)
        devices = bug_device_type_choices()
        device = str(params.get('device_type') or '')
        device = device if device in devices else ''
        return Response({
            'status': status,
            'type': issue_type,
            'priority': priority,
            'device_type': device,
            'counts': _counts(bug_report_counts()),
            'status_choices': _choices(BugReport.STATUS_CHOICES),
            'issue_types': _choices(BugReport.ISSUE_TYPES),
            'priority_choices': _choices(BugReport.PRIORITY_CHOICES),
            'device_type_choices': [{'value': d, 'label': d.capitalize()} for d in devices],
            'reports': [{
                **_bug_row(r, request.user),
                # The admin table's extra columns.
                'device_type': r.device_type,
                'browser': r.browser,
                'operating_system': r.operating_system,
                'admin_notes': r.admin_notes,
                'resolved_at': _iso(r.resolved_at),
                'resolved_by': r.resolved_by.name if r.resolved_by_id else None,
            } for r in tracked_bug_reports(status, issue_type, priority, device)],
            'links': {'page': reverse('bug_admin')},
        })


class BugAdminUpdateView(MobileAPIView):
    web_page = 'bug_admin_update'
    web_page_toggle = False   # @bug_admin_required only

    def post(self, request, bug_id):
        _require_bug_admin(request.user)
        report = BugReport.objects.filter(id=bug_id).first()
        if report is None:
            raise exceptions.NotFound()
        body = _body(request)
        update_bug_report(report, request.user, _text(body.get('status')), _text(body.get('admin_notes')))
        return Response(_bug_detail(bug_report_for_detail(report.id), request.user))


def _feedback_row(f, user):
    return {
        'id': f.id,
        'request_type': f.request_type,
        'request_type_label': f.get_request_type_display(),
        'title': f.title,
        'description': f.description,
        'page': f.page,
        'page_label': f.get_page_display() if f.page else '',
        'priority': f.priority,
        'priority_label': f.get_priority_display(),
        'status': f.status,
        'status_label': f.get_status_display(),
        'submitted_by': _name(f.submitted_by),
        'is_mine': f.submitted_by_id == user.pk,
        'submitted_at': _iso(f.submitted_at),
        'has_attachment': bool(f.attachment),
        'links': {'detail': reverse('feedback_request_detail', args=[f.id])},
    }


def _feedback_detail(f, user):
    can_manage = can_manage_feedback(user)
    return {
        'submission': {
            **_feedback_row(f, user),
            'feature': f.feature,
            'page_url': f.page_url,
            'resolved_at': _iso(f.resolved_at),
            'resolved_by': f.resolved_by.name if f.resolved_by_id else None,
            'admin_notes': f.admin_notes if can_manage else None,
            # Only reached once feedback_visible_to passed: the file's rule too.
            'attachment': f'support/feedback/{f.id}/attachment/' if f.attachment else None,
            'attachment_name': os.path.basename(f.attachment.name) if f.attachment else None,
        },
        'can_manage': can_manage,
        'is_owner': f.submitted_by_id == user.pk,
        'status_choices': _choices(FeedbackRequest.STATUS_CHOICES),
        'links': {'page': reverse('feedback_request_detail', args=[f.id]), 'board': reverse('feedback_tracker')},
    }


def _visible_feedback(feedback_id, user):
    """The submission, or 404 when it's missing or someone else's ticket (the page's 404)."""
    f = feedback_for_detail(feedback_id)
    if f is None or not feedback_visible_to(f, user):
        raise exceptions.NotFound()
    return f


class FeedbackDetailView(MobileAPIView):
    web_page = 'feedback_request_detail'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request, feedback_id):
        return Response(_feedback_detail(_visible_feedback(feedback_id, request.user), request.user))


class FeedbackAttachmentView(MobileAPIView):
    web_page = 'feedback_attachment'
    web_page_toggle = False   # the web view has no @require_page_enabled

    def get(self, request, feedback_id):
        f = FeedbackRequest.objects.filter(id=feedback_id).first()
        if f is None or not feedback_visible_to(f, request.user):
            raise exceptions.NotFound()
        return _file(f.attachment)


class FeedbackAdminView(MobileAPIView):
    web_page = 'feedback_admin'
    web_page_toggle = False   # @feedback_admin_required only

    def get(self, request):
        _require_feedback_admin(request.user)
        params = request.query_params
        status = _choice(params.get('status'), FeedbackRequest.STATUS_CHOICES)
        request_type = _choice(params.get('type'), FeedbackRequest.REQUEST_TYPES)
        priority = _choice(params.get('priority'), FeedbackRequest.PRIORITY_CHOICES)
        c = feedback_admin_counts()
        return Response({
            'status': status,
            'type': request_type,
            'priority': priority,
            'counts': {**_counts(c), 'ideas': c['idea_count'], 'tickets': c['ticket_count']},
            'status_choices': _choices(FeedbackRequest.STATUS_CHOICES),
            'request_types': _choices(FeedbackRequest.REQUEST_TYPES),
            'priority_choices': _choices(FeedbackRequest.PRIORITY_CHOICES),
            'submissions': [{
                **_feedback_row(f, request.user),
                'admin_notes': f.admin_notes,
                'resolved_at': _iso(f.resolved_at),
                'resolved_by': f.resolved_by.name if f.resolved_by_id else None,
            } for f in admin_feedback_requests(status, request_type, priority)],
            'links': {'page': reverse('feedback_admin')},
        })


class FeedbackAdminUpdateView(MobileAPIView):
    web_page = 'feedback_admin_update'
    web_page_toggle = False   # @feedback_admin_required only

    def post(self, request, feedback_id):
        _require_feedback_admin(request.user)
        f = FeedbackRequest.objects.filter(id=feedback_id).first()
        if f is None:
            raise exceptions.NotFound()
        body = _body(request)
        update_feedback(f, request.user, _text(body.get('status')), _text(body.get('admin_notes')))
        return Response(_feedback_detail(feedback_for_detail(f.id), request.user))
