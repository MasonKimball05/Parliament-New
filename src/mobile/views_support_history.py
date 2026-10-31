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

Who sees what is each page's rule: a member's own reports and submissions;
the board shows every member's ideas. The detail pages (a bug report's on
the public tracker; a ticket's only to its submitter and the admin) are the
website's rule too. Staff fields (admin notes, who resolved it, the
submitter's browser info) stay out, as on the pages. The board has no
votes. Each detail page (with its screenshot or attachment) and the admin
pages stay on the website: rows carry the paths the app links there.

Every signed-in member (``@login_required`` only, no page toggle), with the
pledge page restrictions of each page.
"""
from django.urls import reverse
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import FeedbackRequest
from src.view.bug_report import own_bug_reports
from src.view.feedback import can_manage_feedback, feature_idea_counts, feature_ideas, own_feedback_requests


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

