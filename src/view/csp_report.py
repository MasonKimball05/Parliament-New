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

We guard against abuse with a hard cap on body size (4 KB), and (v3.46.2)
with a cap on how many reports are STORED: per client IP and site-wide, per
ten-minute window. Over either cap the browser still gets its 204 — browsers
never retry a report, so refusing it changes nothing for them — but no row is
written. The comment above `_WINDOW` says why the caps are where they are.
"""
import json
import logging

from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest
from django.http.request import RawPostDataException
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from src.utils.security_utils import get_client_ip

logger = logging.getLogger('security')

_MAX_BODY = 4096  # bytes

#: v3.46.2 — storage caps, per `_WINDOW` seconds.
#:
#: Before this, every accepted POST wrote one `CSPViolation` row, and nothing
#: deletes those rows. The endpoint is public and CSRF-exempt, so the only
#: ceiling was nginx's `general_zone` (10 r/s per IP): about 864,000 rows a
#: day from one client, and more from a client that can vary its address.
#:
#: A real browser on a page with a CSP problem sends a handful of reports per
#: page view, and one copy of a violation is as useful as fifty — the admin
#: view groups them by (directive, blocked URI) anyway. 60 per IP per ten
#: minutes is well above that; 1,000 site-wide stops address rotation from
#: getting around the per-IP cap.
_WINDOW = 600
_PER_IP_CAP = 60
_GLOBAL_CAP = 1000


def _bump(key):
    """Increment a cache counter, starting it (with the window's TTL) if missing."""
    if cache.add(key, 1, _WINDOW):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, _WINDOW)
        return 1


def _over_storage_cap(ip_address):
    """
    True when this report should be acknowledged but not stored.

    Both counters are bumped on every report, so a client already over its
    own cap keeps counting toward the global one — that is deliberate: a
    flood should use up the global allowance and stop, not be invisible to it.
    A cache failure must not break the endpoint, so it fails open (stores).
    """
    try:
        per_ip = _bump(f'csp_report:ip:{ip_address or "unknown"}')
        site = _bump('csp_report:all')
    except Exception:
        return False
    over = per_ip > _PER_IP_CAP or site > _GLOBAL_CAP
    # Log the moment a cap is crossed, once — not once per dropped report.
    if per_ip == _PER_IP_CAP + 1:
        logger.warning("CSP report cap reached for IP=%s; further reports from it are not stored "
                       "for up to %d s", ip_address, _WINDOW)
    if site == _GLOBAL_CAP + 1:
        logger.warning("Site-wide CSP report cap reached; reports are not stored for up to %d s", _WINDOW)
    return over


@csrf_exempt  # Browser sends CSP reports directly — no session/cookie context, so CSRF token is unavailable
@require_POST
def csp_report(request):
    """Receive a browser CSP violation report and log it."""
    if int(request.META.get('CONTENT_LENGTH', 0) or 0) > _MAX_BODY:
        return HttpResponseBadRequest()

    # v3.45.1 — this endpoint is public and CSRF-exempt, so anything can be
    # posted to it. Three kinds of body used to raise (a 500 each time):
    #   * a form-encoded or multipart POST: a middleware has already read
    #     request.POST, and Django then refuses to hand out request.body
    #   * JSON that is not an object ([], "a", 5): no .get()
    #   * a field that is not text ({"blocked-uri": 5}): `origin in 5`
    # All three are now a 400, like any other body that is not a report.
    try:
        body = request.body[:_MAX_BODY]
    except RawPostDataException:
        return HttpResponseBadRequest()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest()
    if not isinstance(payload, dict):
        return HttpResponseBadRequest()

    report = payload.get('csp-report') or payload  # Chrome wraps it; Firefox sometimes doesn't
    if not report or not isinstance(report, dict):
        return HttpResponseBadRequest()

    def _text(key):
        value = report.get(key, '')
        return '' if value is None else str(value)

    blocked_uri   = _text('blocked-uri')
    violated      = _text('violated-directive')
    document_uri  = _text('document-uri')
    source_file   = _text('source-file')
    line_number   = _text('line-number')
    ip_address    = get_client_ip(request)

    # Known benign origins injected by infrastructure (Cloudflare beacon, etc.)
    # Downgrade these to 'low' so they don't pollute the security dashboard.
    _NOISE_ORIGINS = (
        'static.cloudflareinsights.com',
        'cloudflareinsights.com',
    )
    is_noise = any(origin in blocked_uri for origin in _NOISE_ORIGINS)
    severity = 'low' if is_noise else 'medium'

    details = (
        f"Blocked URI: {blocked_uri}\n"
        f"Violated directive: {violated}\n"
        f"Document: {document_uri}\n"
        f"Source: {source_file}:{line_number}"
    )

    # v3.46.2 — acknowledged but neither stored nor logged once a cap is
    # reached. Checked before the log lines too: the `security` logger shares
    # one 10 MB x 3 rotating file with django/src (see v3.19.4), so an
    # unthrottled warning per report would push real entries out of it.
    if _over_storage_cap(ip_address):
        return HttpResponse(status=204)

    if is_noise:
        logger.info(
            "CSP violation (known infrastructure) | blocked=%s | directive=%s | document=%s",
            blocked_uri, violated, document_uri,
        )
    else:
        logger.warning(
            "CSP violation | IP=%s | blocked=%s | directive=%s | document=%s",
            ip_address, blocked_uri, violated, document_uri,
        )

    # Log to CSPViolation (non-blocking — ignore DB errors)
    try:
        from src.models import CSPViolation
        CSPViolation.objects.create(
            violated_directive=violated[:200],
            blocked_uri=blocked_uri[:500],
            document_uri=document_uri[:500],
            source_file=source_file[:500],
            line_number=str(line_number)[:20],
            ip_address=ip_address if ip_address else None,
        )
    except Exception:
        pass  # Never let a logging failure break the HTTP response

    # 204 No Content — the spec-recommended response for CSP reports
    return HttpResponse(status=204)
