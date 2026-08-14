import os
import re
import uuid
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from django.utils.text import slugify


def uuid_upload_path(directory):
    """
    Build an `upload_to` callable storing `<directory>/<uuid>.<ext>`.

    ⚠️ v3.19.6 — DEFENCE IN DEPTH, EXPLICITLY NOT THE ACCESS CONTROL. The access
    control is the ownership-aware view in `src/view/serve_private_upload.py`
    plus the directory's entry in `PRIVATE_MEDIA_PREFIXES`. Read those first.

    v3.19.3 wrote that same sentence about `legislation_draft_upload_path` and
    then spent v3.19.5 proving it meant it — the route it named was still open,
    so the random name was doing exactly the work it said it must never do. The
    sentence is only true while the route is shut.

    Why the names needed changing at all: `SanitizedFilenameMixin` below
    slugifies the uploaded filename at save time (v3.14.2), and Django appends
    its random 7-character suffix ONLY on collision. So the first upload of
    `IMG_4471.jpeg` was stored — and served — at `kai_reports/img-4471.jpeg`.
    For directories holding allegation evidence, GPA screenshots and application
    files, a guessable name is a second access control nobody chose.

    ⚠️ `upload_to` IS SAVE-TIME ONLY. Files already on disk keep their slugified
    names; migration `0017` is `AlterField` and touches no data, deliberately —
    a data migration that renames real evidence is the riskiest thing available
    here, and the route being shut is what protects those files. This stops the
    guessable population GROWING. It does not retire it.

    ⚠️ Lives HERE, not beside the models that use it. Django serialises
    `upload_to` into migrations by import path, so each field needs a
    module-level callable with a stable identity — and putting the shared factory
    in one model module would have made every other model module import it,
    which is how `src/models/` acquires import cycles.
    """
    def _upload_to(instance, filename):
        ext = os.path.splitext(filename)[1].lower()
        ext = ''.join(c for c in ext if c.isalnum() or c == '.')
        return f'{directory}/{uuid.uuid4().hex}{ext}'

    return _upload_to


def _reject_browser_executable(name):
    """
    v3.19.7 — refuse to STORE a file whose extension the browser executes.

    ⚠️ THIS IS THE LAYER NO WRITER CAN FORGET, AND IT EXISTS BECAUSE FOUR
    FORGOT. `validate_uploaded_file` is the real validation — allowlist, size,
    and a MIME sniff that must agree with the extension — and the four writers
    that skipped it entirely (both slating upload paths and the Kai and
    service-hours custom-field writers) skipped it silently, because a view that
    assigns `request.FILES[...]` straight to a model field looks exactly like a
    view that validated first. There is no `grep` that distinguishes them.

    Every upload in this application, from every writer, passes through
    `Storage.get_valid_name`. So the smallest guarantee that cannot be bypassed
    lives here. It is deliberately a BLOCKLIST and not the allowlist: the
    allowlist in `file_validation.py` has no audio types, and songbook uploads
    are legitimate — a global allowlist at this layer would break real features
    for no security gain, since the per-view validation already applies one
    where it belongs.

    ⚠️ WHAT THIS DOES AND DOES NOT PROMISE. It stops a `.html`/`.svg`/`.js`
    reaching disk. It does NOT check content, so a `.pdf` full of HTML still
    lands — that is `validate_uploaded_file`'s job, and the reason both layers
    exist. And it is not the mitigation either: the mitigation is that
    `serve_private_upload` only renders an allowlist of content types inline.
    Three layers, deliberately, because the file that gets through any two of
    them should still be harmless.

    Raises `SuspiciousFileOperation` (a subclass of Exception that Django's file
    handling already anticipates) rather than `ValidationError`, because this is
    reached during `save()` and not during form cleaning — a caller that wants a
    friendly message must validate before saving, which is the point.
    """
    from django.core.exceptions import SuspiciousFileOperation

    # Local import: `file_validation` imports python-magic at module scope, and
    # this module is imported by every model module at startup. The constant is
    # not duplicated here on purpose — one blocklist, two enforcement points.
    from src.utils.file_validation import BLOCKED_EXTENSIONS

    ext = os.path.splitext(name.lower())[1]
    if ext in BLOCKED_EXTENSIONS:
        raise SuspiciousFileOperation(
            f'Refusing to store "{name}": the extension "{ext}" is one the '
            f'browser executes, and uploads are served back from this origin. '
            f'See src/utils/file_validation.py.'
        )


class SanitizedFilenameMixin:
    """v3.14.2 — slugify uploaded filenames at save time.

    Why: filenames land in Content-Disposition headers, X-Accel-Redirect
    URIs, and hand-written links; spaces/quotes/non-ASCII made every one of
    those a special case (07-19 review). Sanitizing once at save kills the
    whole class for future uploads.

    `get_valid_name` is the Django hook for exactly this: it receives the
    bare filename (upload_to directories are handled separately) and the
    result still goes through `get_available_name`, so collisions get
    Django's usual random 7-char suffix — no timestamp prefix needed.
    Existing files on disk keep their stored names.
    """

    def get_valid_name(self, name):
        stem, ext = os.path.splitext(name)
        stem = slugify(stem) or 'file'
        ext = re.sub(r'[^a-z0-9]', '', ext.lower())
        cleaned = f'{stem}.{ext}' if ext else stem
        _reject_browser_executable(cleaned)
        return cleaned


class SanitizedFileSystemStorage(SanitizedFilenameMixin, FileSystemStorage):
    """Default storage (see settings.STORAGES) — plain FS + sanitized names."""


class DualLocationStorage(SanitizedFilenameMixin, FileSystemStorage):
    """
    Custom storage that checks both media and exportable_media folders.

    When retrieving a file:
    1. First checks the regular media folder
    2. If not found, checks the exportable_media folder
    3. Returns the first location where the file exists

    When saving a file:
    - Always saves to the regular media folder
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Define the exportable_media location
        self.exportable_location = os.path.join(settings.BASE_DIR, 'exportable_media')

    def path(self, name):
        """
        Return the filesystem path where the file can be retrieved.
        Checks both media and exportable_media locations.
        """
        # First check the regular media location
        regular_path = super().path(name)
        if os.path.exists(regular_path):
            return regular_path

        # If not found, check exportable_media
        exportable_path = os.path.join(self.exportable_location, name)
        if os.path.exists(exportable_path):
            return exportable_path

        # If not found in either location, return the regular path
        # (this maintains normal behavior for new files)
        return regular_path

    def exists(self, name):
        """
        Check if a file exists in either media or exportable_media.
        """
        # Check regular media location
        if super().exists(name):
            return True

        # Check exportable_media location
        exportable_path = os.path.join(self.exportable_location, name)
        return os.path.exists(exportable_path)

    def url(self, name):
        """
        Return the URL where the file can be accessed.
        Uses the regular URL for both locations since Django will serve from either.
        """
        return super().url(name)
