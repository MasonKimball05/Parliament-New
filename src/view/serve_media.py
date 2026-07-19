"""
v3.14.1 — authenticated serving for /media/ (uploaded files).

Why this exists: Django never served /media/ in production (only the
DEBUG-mode static() helper did, in dev) — nginx served the folder directly,
with NO authentication. Confirmed live on 07-18-26: legislation PDFs,
including the Kai binder, were downloadable anonymously by URL. Uploaded
legislation can contain sensitive chapter material, so all of /media/ now
requires a logged-in member.

Deploy requires an nginx change (see docs/RESTORE.md "Media serving" note and
changelogs/v3.14.1.md): remove/rename the public `location /media/` block so
requests reach Django. Optionally set MEDIA_ACCEL_PREFIX (e.g.
"/internal_media") and add a matching `internal` alias location in nginx —
Django then answers with X-Accel-Redirect and nginx streams the file
(kernel-speed downloads, auth still enforced). Without it, Django streams the
file itself via FileResponse — fine at chapter scale.
"""
import mimetypes
import os
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.utils.http import content_disposition_header

# Optional nginx X-Accel-Redirect fast path. Empty = serve via FileResponse.
MEDIA_ACCEL_PREFIX = os.getenv('MEDIA_ACCEL_PREFIX', '')


@login_required
def serve_media(request, path):
    """Serve one uploaded file to a logged-in member."""
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    resolved = os.path.realpath(os.path.join(media_root, path))

    # Directory-traversal guard (same pattern as serve_exportable_media)
    if not resolved.startswith(media_root + os.sep):
        raise Http404('File not found')
    if not os.path.isfile(resolved):
        raise Http404('File not found')

    content_type, _ = mimetypes.guess_type(resolved)
    content_type = content_type or 'application/octet-stream'

    if MEDIA_ACCEL_PREFIX:
        response = HttpResponse(content_type=content_type)
        # v3.14.2: quote the path — spaces/%/non-ASCII in an uploaded
        # filename would otherwise produce an invalid internal URI.
        response['X-Accel-Redirect'] = f'{MEDIA_ACCEL_PREFIX}/{quote(path)}'
    else:
        response = FileResponse(open(resolved, 'rb'), content_type=content_type)

    # v3.14.2: RFC 5987-safe filename (handles quotes + non-ASCII);
    # was a raw f-string that broke on a `"` in the filename.
    response['Content-Disposition'] = content_disposition_header(
        as_attachment=False, filename=os.path.basename(resolved))
    # private: member-only content must never land in shared caches
    # (Cloudflare cached these PDFs publicly before this fix).
    response['Cache-Control'] = 'private, max-age=3600'
    return response
