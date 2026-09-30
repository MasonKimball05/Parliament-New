"""
Static-asset fingerprints for the site monitor (v3.37.0, 09-30-26).

Why: deploy step 7 is "purge the Cloudflare cache if static files changed".
Cloudflare caches /static/css/tailwind.css (nginx sends `public, immutable`
for 30 days, and the URL has no hash), so after a deploy that changes it,
browsers can get the OLD file, or a cached 404 from before the file existed,
while curl against the origin looks fine. That has caused "no styling" bugs.

go-sentinel fetches each same-site /static/*.css and *.js file THROUGH
Cloudflare, then asks this endpoint for the SHA-256 of the same file as it
sits in STATIC_ROOT on the origin (what nginx serves). If they differ,
Cloudflare is serving a stale copy and needs a purge.

  GET /site-monitor/static-assets/?path=css/tailwind.css&path=js/app.js
  X-Site-Monitor-Token: <SITE_MONITOR_TOKEN>

→ {"css/tailwind.css": {"sha256": "…", "bytes": 12345}, "js/app.js": null}

Without the token, including when no token is configured, this is a plain 404:
the endpoint is invisible. Only .css/.js files inside STATIC_ROOT are
answered, and at most 20 per request. The files are public anyway; the gate
keeps the endpoint from being an unauthenticated file-hashing service.
"""
import hashlib
import os

from django.conf import settings
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET

from src.site_monitor import is_site_monitor_request

ALLOWED_EXTENSIONS = ('.css', '.js')
MAX_PATHS = 20


def _fingerprint(root, rel):
    """{'sha256', 'bytes'} for STATIC_ROOT/rel, or None if not a servable asset."""
    if not rel.endswith(ALLOWED_EXTENSIONS):
        return None
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        return None
    h = hashlib.sha256()
    with open(full, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return {'sha256': h.hexdigest(), 'bytes': os.path.getsize(full)}


@require_GET
def static_asset_fingerprints(request):
    if not is_site_monitor_request(request):
        raise Http404()
    root = os.path.realpath(str(settings.STATIC_ROOT))
    paths = [p.lstrip('/') for p in request.GET.getlist('path')][:MAX_PATHS]
    static_url = (settings.STATIC_URL or '/static/').lstrip('/')
    result = {}
    for p in paths:
        rel = p[len(static_url):] if static_url and p.startswith(static_url) else p
        result[p] = _fingerprint(root, rel)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response
