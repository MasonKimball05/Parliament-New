"""
CSP violation reporting endpoint.

Browsers send a POST to /csp-report/ when a Content-Security-Policy violation
occurs (script/style blocked by the browser's enforcer).  We log each report
to SecurityNotificationLog so they appear in the Admin-v2 security dashboard
alongside other security events.

The endpoint is intentionally public (no login required) because:
  - The browser sends reports before any JavaScript runs
  - Reports are sent from within a page context, so CSRF isn't applicable
  - Each report is at most a few hundred bytes of JSON

We guard against abuse with a hard cap on body size (4 KB).
"""
import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from src.utils.security_utils import get_client_ip

logger = logging.getLogger('security')

_MAX_BODY = 4096  # bytes


@csrf_exempt
@require_POST
def csp_report(request):
    """Receive a browser CSP violation report and log it."""
    if int(request.META.get('CONTENT_LENGTH', 0) or 0) > _MAX_BODY:
        return HttpResponseBadRequest()

    body = request.body[:_MAX_BODY]
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest()

    report = payload.get('csp-report') or payload  # Chrome wraps it; Firefox sometimes doesn't
    if not report or not isinstance(report, dict):
        return HttpResponseBadRequest()

    blocked_uri   = report.get('blocked-uri', '')
    violated      = report.get('violated-directive', '')
    document_uri  = report.get('document-uri', '')
    source_file   = report.get('source-file', '')
    line_number   = report.get('line-number', '')
    ip_address    = get_client_ip(request)

    details = (
        f"Blocked URI: {blocked_uri}\n"
        f"Violated directive: {violated}\n"
        f"Document: {document_uri}\n"
        f"Source: {source_file}:{line_number}"
    )

    logger.warning(
        "CSP violation | IP=%s | blocked=%s | directive=%s | document=%s",
        ip_address, blocked_uri, violated, document_uri,
    )

    # Log to SecurityNotificationLog (non-blocking — ignore DB errors)
    try:
        from src.models import SecurityNotificationLog
        SecurityNotificationLog.objects.create(
            event_type='csp_violation',
            severity='medium',
            details=details,
            ip_address=ip_address if ip_address else None,
            email_sent=False,
        )
    except Exception:
        pass  # Never let a logging failure break the HTTP response

    # 204 No Content — the spec-recommended response for CSP reports
    return HttpResponse(status=204)
