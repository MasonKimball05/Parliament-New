"""
v3.19.8 — what an uploaded file is allowed to BECOME when a member opens it.

One rule, one place, both views that serve uploads.

WHY THIS MODULE EXISTS
----------------------
v3.19.7 got this right and got it right in one view. `INLINE_SAFE_CONTENT_TYPES`
lived inside `src/view/serve_private_upload.py`, which serves **six** private
directories to a Kai reviewer or a slating committee member. `serve_media`, which
serves **ten public ones to every logged-in member in the chapter**, kept
`as_attachment=False` and a content type guessed from a member-supplied filename,
exactly as it has since v3.14.1.

That is the same shape CLAUDE.md has now recorded seven times — *a rule stated
correctly, a helper written to enforce it, then something left outside the
helper* — and this time the thing left outside was the **larger** surface. The
private views were the ones being fixed, so they were the ones that got the fix.

⚠️ **IT IS NOT CURRENTLY EXPLOITABLE, AND THAT IS NOT WHY THIS IS SAFE.** What
shuts it today is `_reject_browser_executable` in `src/storage.py`, which refuses
to store a `.html`/`.svg`/`.js` from **any** writer, public prefixes included —
plus the measured fact that zero such files exist under `media/` or
`exportable_media/`. So the protection is real and it is **incidental**: it comes
from a blocklist of extensions somebody thought of, one layer down, rather than
from a decision about what this response renders. The next content type a browser
learns to execute arrives already permitted here.

**A blocklist protects you from the files you named. An allowlist protects the
response.** Both layers stay.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
⚠️ `image/svg+xml` is NOT in the set. An SVG is an XML document that may contain
`<script>`; it is an image everywhere except in the way that matters here. That
single exclusion is the entire reason this enumerates rather than testing
`content_type.startswith('image/')`.

⚠️ Audio is NOT in the set either, and the songbook still works. `<audio>` and
`<img>` load their sources as **subresources**, and `Content-Disposition` is
ignored for subresource loads — so a player embedded in a page is unaffected by
anything decided here. The header only changes what happens when someone
navigates to the file's URL directly, and for an `.mp3` that is a download, which
is fine. (`serve_song_audio` is a separate view with its own route and is not
touched by this module at all; `templates/songbook_detail.html` points at
`{% url 'song_audio' %}`, not at `/media/songbook/`.)
"""

from django.utils.http import content_disposition_header

#: Content types this application will render in the browser. Everything else is
#: sent as a download.
#:
#: PDFs and raster images render because that is what the pages need — the
#: bug-report screenshot is an `<img src>`, reviewers preview PDFs, and profile
#: pictures and landing photos are `<img>` tags on public prefixes.
#:
#: **This is a classification, so it is an allowlist.** A blocklist of "types we
#: render unsafely" is a list of the ones somebody thought of.
INLINE_SAFE_CONTENT_TYPES = frozenset({
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/bmp',
    'image/tiff',
})


def should_download(content_type):
    """
    True if this content type must be sent as an attachment rather than rendered.

    `application/octet-stream` lands here, which is the right default direction:
    a type we could not identify is a type we have not reasoned about.
    """
    return content_type not in INLINE_SAFE_CONTENT_TYPES


def apply_disposition(response, content_type, filename):
    """
    Set `Content-Disposition` and `X-Content-Type-Options` on an upload response.

    Both callers go through here so that the disposition decision and the
    nosniff header cannot drift apart — the header is what stops a browser
    disregarding a `Content-Type` it disagrees with, so a response that decided
    to render inline without it has decided nothing.

    `SECURE_CONTENT_TYPE_NOSNIFF` sets the same header globally. It is stated
    locally as well because these are the only responses in the application whose
    body is member-supplied AND whose type is guessed from a member-supplied
    name, and that guarantee should not depend on a setting someone turns off for
    an unrelated reason.

    Uses `content_disposition_header` (RFC 5987) rather than an f-string — v3.14.2
    fixed that here and it must not come back; a `"` or a non-ASCII character in
    an uploaded filename breaks the raw form.
    """
    response['Content-Disposition'] = content_disposition_header(
        as_attachment=should_download(content_type),
        filename=filename,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    return response
