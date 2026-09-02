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

from ..utils.content_disposition import apply_disposition

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
#:
#: ⚠️ v3.19.6 — AND THE COMMENT ABOVE WAS WRONG ABOUT "THE FIRST SUCH THING".
#: `legislation_drafts/` was the ninth. v3.19.5 stated the property correctly,
#: built the right mechanism, and put one entry in it — because nothing ever
#: enumerated the population the set is drawn from. Walking
#: `apps.get_models()` for `FileField`/`ImageField` took four minutes and found
#: eight more directories under a narrower promise, four of them holding the
#: most confidential material this application handles: Kai allegation
#: attachments, slating GPA screenshots and application files, and excuse
#: documents (the help text on that field says "doctor note").
#:
#: **The durable lesson, and it is not the same as v3.19.5's:** building the
#: general mechanism is not applying it to the general case. A set is only the
#: general form if something enumerates the population — otherwise it is an `if`
#: with better manners. That enumeration is now
#: `src/test_media_classification.py`, which fails when any model gains an
#: `upload_to` that appears in neither this set nor `PUBLIC_MEDIA_PREFIXES`.
PRIVATE_MEDIA_PREFIXES = frozenset({
    # → src.view.legislation_drafts.serve_legislation_draft_document
    'legislation_drafts',
    # → src.view.serve_private_upload, one view each. The trailing directories
    #   (`kai_reports/custom_fields/` etc.) are covered by their first segment,
    #   which is what serve_media compares.
    'kai_reports',
    # v3.28.8 — kai_accommodations/ (attachment + custom_fields/ file
    # responses), same reasoning as kai_reports/ one line up: an
    # accommodation request is often medical/religious/disability
    # information about a named member. See serve_kai_accommodation_attachment
    # and serve_kai_accommodation_response_file.
    'kai_accommodations',
    'slating',
    'excuse_documents',
    'service_hours',
    'bug_reports',
})

#: Directories under MEDIA_ROOT that `/media/`'s promise is CORRECT for.
#:
#: v3.19.6 — the other half of the classification, and it exists so that the
#: absence of a decision is a build failure rather than a default. Membership
#: here is a positive statement: *any logged-in member may read this*. Uploaded
#: legislation, chapter minutes and committee documents are chapter business;
#: profile pictures, songbook audio and landing photos are chapter-visible by
#: intent.
#:
#: ⚠️ The four `committee_*` / `document_versions` entries are the ones to
#: revisit first if this is ever reopened. CLAUDE.md records that
#: `manage_chapter_document` was tightened on 07-22-26 to guard delete/edit by
#: *committee ownership*, which is an argument that the READ side may be
#: narrower than chapter-wide too. The 08-10 review declined to assert either
#: way and neither does this line — they are classified public because that is
#: the behaviour that has always shipped, not because anyone has ruled on it.
PUBLIC_MEDIA_PREFIXES = frozenset({
    'legislation_docs',
    'committee_minutes',
    'committee_documents',
    'committee_legislation',
    'document_versions',
    'profile_pictures',
    'songbook',
    'landing_photos',
    'passed_resolutions',
    'og_images',
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
    #
    # ⚠️ v3.19.8 — AND SINCE v3.14.1 THIS SAID `as_attachment=False` FOR EVERY
    # TYPE. v3.19.7 built the inline allowlist for the six PRIVATE directories
    # and left the ten public ones here, which is the larger surface by every
    # measure that matters: `serve_media` answers every logged-in member in the
    # chapter, where `serve_private_upload` answers a committee of four.
    #
    # It was never exploitable, because `_reject_browser_executable` refuses to
    # store a `.html`/`.svg`/`.js` from any writer — but that is a blocklist of
    # extensions somebody thought of, one layer down, and it is not a decision
    # about what THIS response renders. Both layers stay; see
    # `src/utils/content_disposition.py`.
    #
    # Applied in BOTH branches, deliberately. In X-Accel mode nginx streams the
    # body and Django supplies only the headers, so this is the only place the
    # disposition can be set at all — and X-Accel is the production path, i.e.
    # the one that would have gone unchecked if this sat under the `else`.
    apply_disposition(response, content_type, os.path.basename(resolved))
    # private: member-only content must never land in shared caches
    # (Cloudflare cached these PDFs publicly before this fix).
    response['Cache-Control'] = 'private, max-age=3600'
    return response
