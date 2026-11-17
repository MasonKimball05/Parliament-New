"""
Document files the app shows in its own viewer (v4.4.14, 11-17-26).

Until now three documents opened as website pages inside the app's web view:
a bill's attached document, an excuse's supporting document and the
Constitution & Bylaws PDF. These endpoints hand the app the file itself (with
the sign-in token, like documents/<id>/file/), so the native document screen
shows it and offers Share / Save / Print.

* ``GET legislation/<id>/document/file/`` — ``view_legislation_document``'s
  file. The web page asks only for sign-in, so this does too.
* ``GET excuses/<id>/document/file/`` — ``serve_excuse_document``: the excuse's
  author, or an officer or chair, with the excuse system on. Health
  information, so the same rule as the web and nothing wider.
* ``GET cnb/pdf/`` — ``cnb_document_pdf``: the live-generated PDF of every
  enabled governing document, for any signed-in member.

Each mirrors its web view's pledge restriction (check_web_page on the web
url_name); none of those pages has a page toggle.

* ``GET site-file/<site path>`` — any other uploaded file the payloads link
  by its site path (Kai and service-hours attachments and form file answers,
  a slating GPA screenshot, a draft's document, a resolution's /media/ file).
  The path is resolved and, only if it names one of SITE_FILE_VIEWS, handed to
  that web view itself, so each file keeps exactly its website access rule
  (the views are already the single source of truth for who may read what;
  see src/view/serve_private_upload.py). Anything else, a redirect (a refused
  feature flag) or an error is a 404.
"""
from zoneinfo import ZoneInfo

from django.http import Http404, HttpResponse
from django.urls import Resolver404, resolve
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.views import APIView

from src.mobile.api import MobileAPIView, check_web_page
from src.models import AttendanceExcuse, Legislation
from src.view.serve_private_upload import _stream_private_file


def _private(response):
    response['Cache-Control'] = 'private, no-store'
    return response


class LegislationDocumentFileView(MobileAPIView):
    web_page = 'view_document'
    web_page_toggle = False   # view_legislation_document is @login_required only

    def get(self, request, legislation_id):
        leg = Legislation.objects.filter(pk=legislation_id).only('id', 'document').first()
        if leg is None or not leg.document:
            raise exceptions.NotFound()
        return _private(_stream_private_file(leg.document))


class ExcuseDocumentFileView(MobileAPIView):
    web_page = 'excuse_document'
    web_page_toggle = False   # @require_feature_flag, no @require_page_enabled

    def get(self, request, excuse_id):
        from src.feature_flag_decorators import check_feature_enabled
        from src.permissions import user_is_officer_or_chair
        if not (check_feature_enabled('attendance_tracking') and check_feature_enabled('excuse_system')):
            raise exceptions.NotFound()
        x = AttendanceExcuse.objects.filter(pk=excuse_id).only('id', 'user_id', 'supporting_document').first()
        if x is None or not x.supporting_document:
            raise exceptions.NotFound()
        if not (x.user_id == request.user.pk or user_is_officer_or_chair(request.user)):
            raise exceptions.NotFound()
        return _private(_stream_private_file(x.supporting_document))


class CnbPdfView(MobileAPIView):
    web_page = 'cnb_document_pdf'
    web_page_toggle = False   # @login_required only

    def get(self, request):
        from src.view.officer.cnb import generate_cnb_document_pdf_buffer
        buf = generate_cnb_document_pdf_buffer()
        generated = timezone.now().astimezone(ZoneInfo('America/Chicago'))
        name = f"Constitution_and_Bylaws_{generated.strftime('%Y-%m-%d')}.pdf"
        response = HttpResponse(buf.read(), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{name}"'
        response['X-Content-Type-Options'] = 'nosniff'
        return _private(response)


#: Web views that stream one uploaded file and decide access themselves. Only
#: these may be reached through site-file/; never a page, never a form.
SITE_FILE_VIEWS = frozenset({
    'kai_report_attachment', 'kai_response_file',
    'kai_commendation_attachment', 'kai_commendation_response_file',
    'slating_gpa_screenshot', 'slating_response_file',
    'excuse_document',
    'service_hours_attachment', 'service_hours_response_file',
    'bug_report_screenshot', 'feedback_attachment',
    'legislation_draft_document',
    'serve_media',   # members' uploads; it refuses PRIVATE_MEDIA_PREFIXES itself
})


class SiteFileView(MobileAPIView):
    #: The guard test wants a web page name; the real one depends on the path,
    #: so get() checks the resolved view's page and initial() skips the default.
    web_page = 'serve_media'
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        APIView.initial(self, request, *args, **kwargs)

    def get(self, request, site_path):
        path = '/' + site_path
        if '..' in path or '\\' in path:
            raise exceptions.NotFound()
        try:
            match = resolve(path)
        except Resolver404:
            raise exceptions.NotFound()
        if match.url_name not in SITE_FILE_VIEWS:
            raise exceptions.NotFound()
        check_web_page(request.user, match.url_name, toggle=False)
        # DRF has set request._request.user to the token's member, so the web
        # view's @login_required and its own access rule see the same person.
        try:
            response = match.func(request._request, *match.args, **match.kwargs)
        except Http404:
            raise exceptions.NotFound()
        if response.status_code != 200:
            if hasattr(response, 'close'):
                response.close()
            raise exceptions.NotFound()
        return _private(response)
