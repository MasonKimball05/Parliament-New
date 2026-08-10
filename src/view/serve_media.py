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


#: ⚠️ v3.19.5 — DIRECTORIES UNDER MEDIA_ROOT THAT THIS VIEW MUST NOT SERVE.
#:
#: `/media/` makes exactly one promise: *any logged-in member may read this*.
#: That is correct for uploaded legislation, minutes, songbook audio and profile
#: pictures — all of it is chapter-visible by intent. It is NOT correct for
#: anything stored under a narrower promise, and `legislation_drafts/` is the
#: first such thing in this codebase ("Only you can see it until you publish it",
#: said in four places).
#:
#: **This set exists because removing the LINK is not removing the ROUTE.**
#: v3.19.3 fixed the draft exposure by building `serve_legislation_draft_document`
#: — author-scoped, correct — and repointing both templates at it. The 08-08
#: review confirmed no template still references `draft.document.url` and closed
#: the finding. But `media/<path:path>` was never touched, so every draft
#: attachment stayed one guessed filename away from any authenticated member,
#: and the uuid `upload_to` that v3.19.3 labelled *"defence in depth, explicitly
#: NOT the access control"* was silently promoted into being the access control.
#: Files predating migration `0016` are worse off still: their names are
#: `slugify()` of the uploaded filename, and `0016` deliberately declined to
#: rename them **on the reasoning that the name was never what protected them** —
#: a statement that is true only once this set exists.
#:
#: So the rule, and it generalises past this one directory: **a new upload
#: directory inherits /media/'s promise by default. If that is the wrong promise,
#: it belongs here AND needs its own ownership-aware view.** Adding the view
#: without adding the entry is the bug this set was written for.
PRIVATE_MEDIA_PREFIXES = frozenset({
    # → src.view.legislation_drafts.serve_legislation_draft_document
    'legislation_drafts',
})


@login_required
def serve_media(request, path):
    """Serve one uploaded file to a logged-in member."""
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    resolved = os.path.realpath(os.path.join(media_root, path))

    # Directory-traversal guard (same pattern as serve_exportable_media)
    if not resolved.startswith(media_root + os.sep):
        raise Http404('File not found')

    # ⚠️ v3.19.5 — CHECKED ON THE RESOLVED PATH, NOT ON `path`, and the ordering
    # is the whole point. `legislation_drafts/x.pdf` and
    # `legislation_docs/../legislation_drafts/x.pdf` are the same file and only
    # the first has a matching first segment before `realpath` runs. Checking the
    # input rather than the value it resolves to is the same shape as the finding
    # this fix closes — so the check sits AFTER the traversal guard, where
    # `relpath` is guaranteed to produce a segment inside MEDIA_ROOT.
    #
    # 404 and not 403, matching `_get_own_draft`: whether a given draft exists is
    # itself author-private, and a 403 answers that question.
    if os.path.relpath(resolved, media_root).split(os.sep)[0] in PRIVATE_MEDIA_PREFIXES:
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
