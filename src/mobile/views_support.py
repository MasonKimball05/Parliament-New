"""
Report a bug and Feedback in the app (v4.4.0): the website's two forms
(/bug-report/ and /feedback/), saved by the same functions the web views use
(``create_bug_report`` in src/view/bug_report.py, ``create_feedback`` in
src/view/feedback.py), so the checks, the admin email and the activity log
entry are the website's.

* ``GET support/bug-report/`` — the form's choices (issue types, pages,
  priorities, the default priority) and the screenshot limits.
  ``POST`` — a report: ``{description, issue_type, page, priority, feature,
  page_url, steps_to_reproduce, expected_behavior, actual_behavior,
  browser_info}`` as JSON, or the same fields as ``multipart/form-data`` with
  an optional ``screenshot`` image (PNG, JPEG, WebP or GIF, at most 10 MB; the
  v3.51.6 checks). A bad screenshot doesn't lose the report: it's saved
  without it and ``screenshot_problem`` says why. 201 ``{id,
  screenshot_problem}``; 400 ``missing_description``.
* ``GET support/feedback/`` — the choices (idea or support ticket, pages,
  priorities). ``POST`` ``{request_type, title, description, page, priority,
  feature, page_url}`` — 201 ``{id, request_type}``; 400 ``missing_fields``.
  Attachments stay on the website.

Every signed-in member, like the web pages (``@login_required`` only, no page
toggle), with the pledge page restrictions of each.
"""
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.view.bug_report import (
    SCREENSHOT_EXTENSIONS, SCREENSHOT_MAX_BYTES, BugReportInvalid, bug_report_choices, create_bug_report,
)
from src.view.feedback import FeedbackInvalid, create_feedback, feedback_choices


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _body(request):
    # A JSON array or a bare string isn't a form; treat it as empty.
    return request.data if hasattr(request.data, 'get') else {}


class BugReportView(MobileAPIView):
    web_page = 'bug_report'
    web_page_toggle = False   # the web page has no @require_page_enabled
    parser_classes = [JSONParser, MultiPartParser]

    def get(self, request):
        c = bug_report_choices()
        return Response({
            'issue_types': _choices(c['issue_types']),
            'pages': _choices(c['page_choices']),
            'priorities': _choices(c['priority_choices']),
            'default_priority': 'medium',
            'screenshot': {'extensions': list(SCREENSHOT_EXTENSIONS), 'max_bytes': SCREENSHOT_MAX_BYTES},
        })

    def post(self, request):
        try:
            report, screenshot_problem = create_bug_report(
                request, _body(request), request.FILES.get('screenshot'))
        except BugReportInvalid:
            raise BadRequest('Describe what went wrong.', code='missing_description')
        return Response({'id': report.id, 'screenshot_problem': screenshot_problem}, status=201)


class FeedbackView(MobileAPIView):
    web_page = 'feedback_request'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        c = feedback_choices()
        return Response({
            'request_types': _choices(c['request_types']),
            'pages': _choices(c['page_choices']),
            'priorities': _choices(c['priority_choices']),
            'default_priority': 'medium',
        })

    def post(self, request):
        try:
            feedback = create_feedback(request, _body(request))
        except FeedbackInvalid:
            raise BadRequest('Add a title and the details.', code='missing_fields')
        return Response({'id': feedback.id, 'request_type': feedback.request_type}, status=201)
