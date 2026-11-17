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
"""
from zoneinfo import ZoneInfo

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import exceptions

from src.mobile.api import MobileAPIView
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
