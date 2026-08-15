"""
Secure file upload validation

Validates file uploads to prevent:
- Malicious file uploads (executables, scripts)
- File type spoofing
- Oversized files
- Double extensions (.pdf.exe)

Usage:
    from src.utils.file_validation import validate_uploaded_file

    try:
        validate_uploaded_file(request.FILES['document'])
        # File is safe, proceed with upload
    except ValidationError as e:
        messages.error(request, str(e))
"""

import os
import magic
from django.core.exceptions import ValidationError
from django.conf import settings


# Maximum file size: 20MB (configurable in settings)
MAX_FILE_SIZE = getattr(settings, 'FILE_UPLOAD_MAX_MEMORY_SIZE', 20 * 1024 * 1024)

# Allowed file extensions and their MIME types
ALLOWED_FILE_TYPES = {
    # Documents
    #
    # ⚠️ v3.19.8 — THE THREE LEGACY OLE2 ENTRIES CARRY GENERIC TYPES AS WELL AS
    # THEIR SPECIFIC ONE, AND THIS IS THE ONE PLACE IN THIS MAP THAT IS NOT
    # BACKED BY A FIXTURE. `.doc`/`.xls`/`.ppt` are OLE2 compound documents;
    # libmagic reports them as `application/msword` when it recognises the
    # internal streams and as `application/x-ole-storage`, `vnd.ms-office` or
    # `CDFV2` when it only recognises the container — which varies by libmagic
    # version, i.e. by which machine the check runs on. Listing only the
    # specific type is exactly the shape that made `.xlsx` reject every real
    # spreadsheet. See `NO_FIXTURE` in `src/test_upload_type_fixtures.py`: a
    # valid OLE2 file cannot be built without a dependency this project does not
    # have, so the mapping is pinned by a test and the file is not. That is
    # weaker and it is recorded as weaker.
    #
    # The cost of the generic types is that a `.xls` renamed `.doc` passes. Both
    # are Office documents the browser downloads, so the interesting question —
    # is this markup wearing a document extension — is still answered.
    '.pdf': ['application/pdf'],
    '.doc': ['application/msword', 'application/x-ole-storage',
             'application/vnd.ms-office', 'application/CDFV2'],
    '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    '.odt': ['application/vnd.oasis.opendocument.text'],

    # Spreadsheets
    '.xls': ['application/vnd.ms-excel', 'application/x-ole-storage',
             'application/vnd.ms-office', 'application/CDFV2'],
    '.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    '.ods': ['application/vnd.oasis.opendocument.spreadsheet'],
    '.csv': ['text/csv', 'text/plain', 'application/csv'],

    # Presentations
    '.ppt': ['application/vnd.ms-powerpoint', 'application/x-ole-storage',
             'application/vnd.ms-office', 'application/CDFV2'],
    '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
    '.odp': ['application/vnd.oasis.opendocument.presentation'],

    # Text files
    '.txt': ['text/plain'],
    '.md': ['text/plain', 'text/markdown'],
    '.rtf': ['application/rtf', 'text/rtf'],
    '.log': ['text/plain', 'text/x-log'],
    '.json': ['application/json', 'text/plain'],
    '.xml': ['application/xml', 'text/xml', 'text/plain'],

    # Images (for reports, documents with photos)
    '.jpg': ['image/jpeg'],
    '.jpeg': ['image/jpeg'],
    '.png': ['image/png'],
    '.gif': ['image/gif'],
    '.webp': ['image/webp'],

    # Archives (for bundled documents)
    '.zip': ['application/zip', 'application/x-zip-compressed'],
}

# Dangerous extensions that should NEVER be allowed
#
# ⚠️ v3.19.7 — THE BLOCKLIST USED TO NAME THINGS THAT RUN ON THE *SERVER* AND
# NOTHING THAT RUNS IN THE *BROWSER*, WHICH IS THE THREAT THIS APPLICATION HAS.
# Parliament never executes an upload; it serves uploads back to members from
# its own origin. `.php` and `.exe` were never reachable here. `.html` and
# `.svg` were: a file served with `Content-Type: text/html` from
# am-parliament.org is a page on am-parliament.org, with the session cookie of
# whoever opened it, and CSP's `script-src 'self'` will happily load a second
# uploaded file as its script. Two of the three most dangerous extensions for
# this codebase were missing while `.jsp` — for a server that has never run
# Java — was present.
#
# The rule this encodes: **an upload blocklist should name what is dangerous
# WHERE THE FILE ENDS UP, not what is dangerous in general.** See
# `src/view/serve_private_upload.py` for the layer that actually decides
# whether a file renders, and `SanitizedFilenameMixin.get_valid_name` in
# `src/storage.py` for the one place every write funnels through.
BLOCKED_EXTENSIONS = {
    # Executables
    '.exe', '.dll', '.bat', '.cmd', '.com', '.msi', '.scr',
    # Scripts
    '.sh', '.bash', '.ps1', '.vbs', '.js', '.mjs', '.jar',
    # Web files that could be executed
    '.php', '.asp', '.aspx', '.jsp', '.cgi',
    # v3.19.7 — markup the BROWSER executes when served from our own origin.
    # `.svg` is in this list for the same reason as `.html`: an SVG is an XML
    # document that may contain <script>, and it is served as image/svg+xml,
    # which browsers render rather than download.
    '.html', '.htm', '.xhtml', '.xht', '.shtml', '.mhtml', '.mht',
    '.svg', '.svgz', '.xsl', '.xslt',
    # Other dangerous
    '.app', '.deb', '.rpm', '.dmg', '.pkg',
}


def get_file_extension(filename):
    """Get lowercase file extension including the dot"""
    return os.path.splitext(filename.lower())[1]


def check_double_extension(filename):
    """
    Check for double extensions like .pdf.exe
    Returns True if suspicious double extension detected
    """
    parts = filename.lower().split('.')
    if len(parts) > 2:
        # Check if any part before the last is a blocked extension
        for part in parts[:-1]:
            if f'.{part}' in BLOCKED_EXTENSIONS:
                return True
    return False


def validate_file_extension(filename):
    """Validate file extension is allowed"""
    ext = get_file_extension(filename)

    # Check for blocked extensions
    if ext in BLOCKED_EXTENSIONS:
        raise ValidationError(
            f'File type "{ext}" is not allowed for security reasons. '
            f'Please upload documents, spreadsheets, or presentations only.'
        )

    # Check for allowed extensions
    if ext not in ALLOWED_FILE_TYPES:
        allowed = ', '.join(sorted(ALLOWED_FILE_TYPES.keys()))
        raise ValidationError(
            f'File type "{ext}" is not allowed. '
            f'Allowed types: {allowed}'
        )

    # Check for double extensions
    if check_double_extension(filename):
        raise ValidationError(
            f'File "{filename}" has a suspicious double extension. '
            f'This is a common malware technique. Please rename the file.'
        )


#: How much of the file `magic` gets to look at.
#:
#: ⚠️ v3.19.8 — WAS 2048, AND THAT ONE NUMBER REJECTED EVERY SPREADSHEET IN THE
#: CHAPTER. An OOXML file is a zip, and libmagic cannot tell WHICH OOXML it is
#: until it has seen entries that sit past the first 2 KB of any real document.
#: Measured on libmagic 5.41 against a real `.xlsx`:
#:
#:     window=  2048 -> application/zip                      ← the old window
#:     window=  8192 -> …spreadsheetml.sheet                 ← the truth
#:
#: `application/zip` is not in `ALLOWED_FILE_TYPES['.xlsx']`, so the answer was
#: "this could be a malicious file", deterministically, for every real
#: spreadsheet. 64 KB costs nothing (the file is already on disk or in memory)
#: and covers the container formats with room to spare.
SNIFF_BYTES = 65536

#: Extensions whose real type lives INSIDE a zip container, and how to read it.
#:
#: ⚠️ WIDENING THE WINDOW WAS NECESSARY AND NOT SUFFICIENT, which is why this
#: exists. A real `.docx` measured on the same libmagic returns
#: `application/octet-stream` at EVERY window size including the whole file and
#: including `magic.from_file` — libmagic's OOXML rules fall back to
#: octet-stream when they recognise the container but not the subtype.
#:
#: The tempting fix is to add `application/zip` and `application/octet-stream`
#: to the `.docx` row. That makes the check pass and makes it meaningless:
#: `application/octet-stream` is what libmagic says about anything it cannot
#: identify, so admitting it admits everything.
#:
#: **So these formats are validated STRUCTURALLY instead — we open the zip and
#: read the type the file declares about itself.** That is the question the MIME
#: map was always pretending to answer, and unlike libmagic's heuristics it does
#: not depend on which version of a system library the server happens to have.
OOXML_MARKERS = {
    '.docx': 'wordprocessingml.document',
    '.xlsx': 'spreadsheetml.sheet',
    '.pptx': 'presentationml.presentation',
}

#: ODF stores its type in a `mimetype` entry, uncompressed and first, precisely
#: so that it can be read without unpacking. (This is why ODF was never broken
#: by the 2 KB window and OOXML was.)
ODF_MIMETYPES = {
    '.odt': 'application/vnd.oasis.opendocument.text',
    '.ods': 'application/vnd.oasis.opendocument.spreadsheet',
    '.odp': 'application/vnd.oasis.opendocument.presentation',
}

#: `[Content_Types].xml` is a manifest, not content. A legitimate one is a few
#: kilobytes; the cap is here so that a crafted entry claiming to decompress to
#: 4 GB cannot be read into memory by the validator that exists to reject it.
_MANIFEST_READ_CAP = 1024 * 1024

#: The entry each zip-backed family declares its type in. `None` = plain `.zip`,
#: where opening the archive was the whole check.
_DECLARATION_ENTRY = {
    **{ext: 'mimetype' for ext in ODF_MIMETYPES},
    **{ext: '[Content_Types].xml' for ext in OOXML_MARKERS},
}


class _ContainerUnreadable(Exception):
    """
    Detection failed: this file could not be read as a zip archive at all.

    Deliberately NOT a `ValidationError`. It carries no verdict — it is the
    signal that `_read_container_declaration` could not answer the question, and
    the caller decides what that means.
    """


def _read_container_declaration(uploaded_file, ext):
    """
    DETECTION ONLY. Return the type string this archive declares about itself,
    or `None` for a plain `.zip`.

    ⚠️ THIS CATCHES `Exception`, AND THAT IS THE v3.19.7 RULE APPLIED RATHER
    THAN BROKEN. v3.19.7's finding was that `validate_mime_type`'s `try`
    contained both the detection AND the verdict, so failing open on a missing
    libmagic also failed open on a real mismatch — *a `try` that contains both
    cannot fail open on one without failing open on the other*. The remedy is to
    separate them, which is what this function is: it contains no `raise
    ValidationError` anywhere, so a broad catch here cannot swallow a verdict,
    because there is no verdict here to swallow.

    ⚠️ AND BROAD IS THE ONLY CORRECT WIDTH, because the alternative is a
    blocklist of a stdlib parser's failure modes. v3.19.8 caught
    `(BadZipFile, OSError, EOFError)` around the `ZipFile()` constructor and left
    the member reads that follow outside it. Measured against the real
    `validate_uploaded_file` on 08-15-26, four ordinary malformations escaped as
    uncaught exceptions — i.e. **HTTP 500 on all 17 upload call sites**, from a
    doctor's note to a Kai attachment:

        unsupported compression method   NotImplementedError
        encrypted zip entry              RuntimeError
        corrupt member (bad CRC)         zipfile.BadZipFile  ← the SAME type the
                                                              constructor's
                                                              handler names,
                                                              four lines later
        encrypted ODF `mimetype`         RuntimeError

    Note the third: enumerating exception types got the type right and the
    *place* wrong, which is the eighth instance of CLAUDE.md's
    "something-left-outside-the-helper" shape and the first where the thing left
    outside was the second half of the same operation.

    The catch logs rather than staying silent, so that a systematic
    misdiagnosis — a Python upgrade changing what `zipfile` raises, or a bug in
    the four lines below — shows up as a run of rejections in the log instead of
    looking like a run of malicious uploads.
    """
    import zipfile

    entry = _DECLARATION_ENTRY.get(ext)
    try:
        archive = zipfile.ZipFile(uploaded_file)
        if entry is None:
            return None
        if entry not in set(archive.namelist()):
            raise _ContainerUnreadable(f'no "{entry}" entry')
        with archive.open(entry) as fh:
            raw = fh.read(_MANIFEST_READ_CAP)
    except _ContainerUnreadable:
        raise
    except Exception as exc:
        import logging
        logging.getLogger('function_calls').warning(
            'Zip-container validation could not read %s (%s): %s: %s',
            uploaded_file.name, ext, type(exc).__name__, exc,
        )
        # `from None` drops the implicit `__context__` chain, and it is not
        # cosmetic: Django's parallel test runner pickles failures across
        # process boundaries, and a chained exception drags a traceback object
        # along. Without `tblib` installed that is unpicklable, and the symptom
        # is `TypeError: cannot pickle 'traceback' object` **instead of** the
        # test failure. (v3.19.9 also adds `tblib` to requirements.txt, which
        # fixes the general case; this stays because it is free.)
        raise _ContainerUnreadable(f'{type(exc).__name__}: {exc}') from None

    return raw.decode('utf-8', 'replace').strip()


def _validate_zip_container(uploaded_file, ext):
    """
    Validate a zip-backed document by reading what it says it is.

    Handles the three OOXML and three ODF extensions, plus plain `.zip` (which
    only has to be a readable zip). Raises `ValidationError` on a mismatch;
    returns normally on success.

    Detection lives in `_read_container_declaration`; every `raise
    ValidationError` lives here. Keeping the verdict out of the function that
    catches is the whole design — see that function's docstring.
    """
    # ⚠️ THE REWIND WRAPS THE WHOLE FUNCTION, NOT JUST THE OPEN, AND A TEST HAD
    # TO SAY SO. The first draft of this put `finally: seek(0)` on the `try`
    # around `ZipFile()` only — correct for the pointer as of that line, and
    # wrong by the time the function returned, because `archive.open(...)`
    # reads the manifest and moves it again. `.docx` came back at offset 413 and
    # `.odt` at 77, so the caller's `save()` would have stored a truncated
    # document. `test_validation_leaves_the_file_pointer_at_the_start` caught it
    # on the first run.
    #
    # **A validator that consumes the stream is a data-loss bug wearing a
    # security fix's clothes**, and it is invisible to every test that only asks
    # whether validation passed.
    uploaded_file.seek(0)
    try:
        try:
            declared = _read_container_declaration(uploaded_file, ext)
        except _ContainerUnreadable:
            raise ValidationError(
                f'File content does not match extension. A "{ext}" file must be a '
                f'valid document archive and this one could not be read. '
                f'This could be a malicious file.'
            ) from None

        if ext in ODF_MIMETYPES:
            if declared != ODF_MIMETYPES[ext]:
                raise ValidationError(
                    f'File content does not match extension. File declares itself '
                    f'"{declared}" but has extension "{ext}". '
                    f'This could be a malicious file.'
                )
            return

        if ext in OOXML_MARKERS:
            if OOXML_MARKERS[ext] not in declared:
                raise ValidationError(
                    f'File content does not match extension. The document inside '
                    f'does not match extension "{ext}". '
                    f'This could be a malicious file.'
                )
            return

        # Plain `.zip` — opening it was the whole check.
    finally:
        uploaded_file.seek(0)


def validate_mime_type(uploaded_file):
    """
    Validate MIME type matches the file extension.

    Two strategies, chosen by extension: zip-backed document formats are opened
    and asked what they are (`_validate_zip_container`); everything else is
    sniffed with python-magic. See `SNIFF_BYTES` and `OOXML_MARKERS` for why
    that split exists — it is not stylistic, libmagic cannot answer for OOXML.
    """
    # Get the declared extension
    ext = get_file_extension(uploaded_file.name)
    allowed_mimes = ALLOWED_FILE_TYPES.get(ext, [])

    if not allowed_mimes:
        raise ValidationError(f'No MIME types defined for {ext}')

    # v3.19.8 — zip-backed formats are validated by reading the container, not
    # by guessing from a prefix of the bytes.
    if ext in OOXML_MARKERS or ext in ODF_MIMETYPES or ext == '.zip':
        _validate_zip_container(uploaded_file, ext)
        return

    # Get actual MIME type from file content.
    #
    # ⚠️ v3.19.7 — THE `except Exception` USED TO WRAP THE `raise` AS WELL AS THE
    # DETECTION, so this function's entire purpose was cancelled by its own error
    # handling: a genuine extension/content mismatch raised `ValidationError`
    # INSIDE the `try`, was caught by `except Exception`, and was downgraded to a
    # log line nobody reads. Every caller believed it was getting a content check
    # and was getting a `logger.warning`.
    #
    # The intent of the broad catch is still right and is kept — python-magic
    # needs libmagic, and a missing system library must not take out every upload
    # form in the application. So the try now covers ONLY the detection call, and
    # the decision is made outside it.
    #
    # **The general form, and it is worth keeping: a `try` that contains both the
    # detection and the verdict cannot fail open on one without failing open on
    # the other.** Narrow the block to the thing that is allowed to fail.
    try:
        # Read a chunk of the file to detect type.
        # v3.19.8: `seek(0)` moved into `finally` — a raising `read()` used to
        # leave the pointer mid-stream for every caller downstream of us.
        try:
            chunk = uploaded_file.read(SNIFF_BYTES)
        finally:
            uploaded_file.seek(0)  # Reset file pointer

        actual_mime = magic.from_buffer(chunk, mime=True)
    except Exception as e:
        # If MIME detection fails, log but don't block
        # (python-magic might not be available on all systems)
        import logging
        logger = logging.getLogger('function_calls')
        logger.warning(f'MIME type validation failed for {uploaded_file.name}: {e}')
        return

    # Check if actual MIME matches allowed MIME types
    if actual_mime not in allowed_mimes:
        raise ValidationError(
            f'File content does not match extension. '
            f'File appears to be "{actual_mime}" but has extension "{ext}". '
            f'This could be a malicious file.'
        )


def validate_file_size(uploaded_file):
    """Validate file size is within limits"""
    if uploaded_file.size > MAX_FILE_SIZE:
        max_mb = MAX_FILE_SIZE / (1024 * 1024)
        actual_mb = uploaded_file.size / (1024 * 1024)
        raise ValidationError(
            f'File size ({actual_mb:.1f}MB) exceeds maximum allowed size ({max_mb:.0f}MB). '
            f'Please upload a smaller file.'
        )

    # Also reject empty files
    if uploaded_file.size == 0:
        raise ValidationError('Uploaded file is empty. Please select a valid file.')


def validate_filename(filename):
    """Validate filename doesn't contain suspicious characters"""
    # Check for path traversal attempts
    if '..' in filename or '/' in filename or '\\' in filename:
        raise ValidationError(
            'Filename contains invalid characters. '
            'Please rename the file without paths or special characters.'
        )

    # Check for null bytes (used in some attacks)
    if '\x00' in filename:
        raise ValidationError('Filename contains null bytes. This file may be malicious.')

    # Ensure filename isn't too long
    if len(filename) > 255:
        raise ValidationError('Filename is too long. Maximum 255 characters.')


def validate_uploaded_file(uploaded_file):
    """
    Comprehensive validation of uploaded file

    Args:
        uploaded_file: Django UploadedFile object (from request.FILES)

    Raises:
        ValidationError: If file fails any validation check

    Returns:
        True if file passes all validation
    """
    if not uploaded_file:
        raise ValidationError('No file was uploaded.')

    filename = uploaded_file.name

    # 1. Validate filename
    validate_filename(filename)

    # 2. Validate file extension
    validate_file_extension(filename)

    # 3. Validate file size
    validate_file_size(uploaded_file)

    # 4. Validate MIME type matches extension
    validate_mime_type(uploaded_file)

    return True


def get_safe_filename(filename):
    """
    Sanitize filename to prevent security issues

    - Removes path components
    - Removes special characters
    - Preserves extension
    - Ensures uniqueness with timestamp if needed
    """
    import re
    from django.utils.text import slugify

    # Get basename (remove any path)
    filename = os.path.basename(filename)

    # Split into name and extension
    name, ext = os.path.splitext(filename)

    # Slugify the name (makes it URL-safe)
    safe_name = slugify(name)

    # If slugify removed everything, use a default
    if not safe_name:
        safe_name = 'document'

    # Combine with extension
    return f"{safe_name}{ext.lower()}"


def validate_image_file(uploaded_file):
    """
    Special validation for image uploads
    More restrictive than document uploads
    """
    allowed_image_types = {
        '.jpg': ['image/jpeg'],
        '.jpeg': ['image/jpeg'],
        '.png': ['image/png'],
        '.gif': ['image/gif'],
        '.webp': ['image/webp'],
    }

    ext = get_file_extension(uploaded_file.name)

    if ext not in allowed_image_types:
        raise ValidationError(
            f'Only image files are allowed: .jpg, .jpeg, .png, .gif, .webp'
        )

    # Rest of validation is the same
    validate_uploaded_file(uploaded_file)
