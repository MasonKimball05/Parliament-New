"""
Terms acceptance and reporting profiles and ideas from the app (11-16-26, App
Store guideline 1.2). Rules: src/terms.py and src/content_reports.py.

* ``POST terms/accept/`` ``{version}`` — the member accepts the Terms of Use;
  ``me/``'s ``terms`` says whether it's needed and which version. 409
  ``terms_changed`` if the app showed an older version.
* ``POST members/<user_id>/report/`` ``{reason, details?}`` — report a profile.
* ``POST support/feedback/ideas/<id>/report/`` ``{reason, details?}`` — report an idea.
  Both: reason is one of ``chat_safety.reasons()``; 409 ``already_reported``.
* ``GET  officer/content-reports/?status=open|dismissed|actioned|all`` and
  ``POST officer/content-reports/<id>/`` ``{action: dismiss|remove, note?}`` —
  officers and admins.

Blocking a member from their profile is ``chat/mutes/`` (views_chat_safety).
"""
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src import content_reports, terms
from src.chat_service import ChatError
from src.mobile.api import MobileAPIView
from src.mobile.views_chat import ChatRefused
from src.mobile.views_excuses_polls import BadRequest
from src.models import ContentReport, FeedbackRequest, ParliamentUser

STATUSES = {'open', 'dismissed', 'actioned', 'all'}


def _refuse(exc):
    if exc.code == 'invalid_input':
        return BadRequest(str(exc), code='invalid_input')
    if exc.status == 404:
        return Http404(str(exc))
    return ChatRefused(exc.code, str(exc), exc.status)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


class TermsAcceptView(MobileAPIView):
    web_page = 'terms'
    web_page_toggle = False

    def post(self, request):
        if not terms.accept(request.user, _body(request).get('version')):
            raise ChatRefused('terms_changed', 'The Terms of Use have changed. Please read them again.', 409)
        return Response({'ok': True, 'version': terms.current_version()})


class ProfileReportView(MobileAPIView):
    web_page = 'member_directory'
    web_page_toggle = False

    def post(self, request, user_id):
        member = ParliamentUser.objects.filter(pk=user_id, is_active=True).first()
        body = _body(request)
        try:
            report = content_reports.report_profile(member, request.user, body.get('reason'),
                                                    body.get('details') or '')
        except ChatError as exc:
            raise _refuse(exc)
        return Response({'ok': True, 'report_id': report.id}, status=201)


class IdeaReportView(MobileAPIView):
    web_page = 'feedback_tracker'
    web_page_toggle = False

    def post(self, request, idea_id):
        idea = FeedbackRequest.objects.filter(pk=idea_id).first()
        body = _body(request)
        try:
            report = content_reports.report_idea(idea, request.user, body.get('reason'),
                                                 body.get('details') or '')
        except ChatError as exc:
            raise _refuse(exc)
        return Response({'ok': True, 'report_id': report.id}, status=201)


class _OfficerBase(MobileAPIView):
    web_page = 'content_reports'
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not (request.user.is_officer or request.user.is_admin):
            raise exceptions.PermissionDenied('Officers only.', code='forbidden')


class ContentReportListView(_OfficerBase):

    def get(self, request):
        status = request.query_params.get('status') or 'open'
        if status not in STATUSES:
            raise BadRequest(f'status must be one of: {", ".join(sorted(STATUSES))}', code='invalid_input')
        return Response({
            'reports': [content_reports.report_json(r) for r in content_reports.officer_queue(status)[:100]],
            'open_count': content_reports.open_count(),
            'auto_hide_threshold': content_reports.AUTO_HIDE_THRESHOLD,
        })


class ContentReportReviewView(_OfficerBase):

    def post(self, request, report_id):
        report = get_object_or_404(ContentReport.objects.select_related('member', 'idea'), pk=report_id)
        body = _body(request)
        try:
            closed = content_reports.review(report, request.user, body.get('action'), body.get('note') or '')
        except ChatError as exc:
            raise _refuse(exc)
        report = content_reports.officer_queue('all').get(pk=report.pk)
        return Response({'report': content_reports.report_json(report), 'closed': closed})
