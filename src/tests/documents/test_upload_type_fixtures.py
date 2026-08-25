"""
v3.19.8 — the accept side of upload validation.

WHY THIS FILE EXISTS
--------------------
`validate_mime_type` had a `raise` trapped inside its own `except Exception` from
the day it was written until v3.19.7 fixed it. For those seven months it could
not reject anything, so `ALLOWED_FILE_TYPES` — the map from extension to the MIME
types libmagic is expected to report — was never exercised by anything. It was a
list of guesses that nobody had reason to check.

v3.19.7 turned it on. The first thing it did was **reject every `.docx` and
`.xlsx` in the chapter**, on all 17 call sites, with the words "This could be a
malicious file". Nothing caught it: `src/test_private_upload_rendering.py` is
thorough about what must be REJECTED — `.html`, `.svg`, `.js` — and asserts
nothing whatever about what must be ACCEPTED.

> **An allowlist that has never rejected anything has also never been tested. A
> validator has two duties and the tests covered one.**

So this module is the other duty, and it is written as an ENUMERATION rather than
a list of cases: `test_every_allowed_extension_has_a_fixture` walks
`ALLOWED_FILE_TYPES` itself and fails the build when someone adds an extension
without adding a sample for it. That is the same move
`src/test_media_classification.py` made for upload directories — a set is only
the general form if something enumerates the population it is drawn from.

WHAT THE FIXTURES ARE
---------------------
Real files, built in-process from the standard library only. No `python-docx`, no
`openpyxl`, no `Pillow`, no committed binaries:

  * the zip-backed formats are built with `zipfile`, which is exactly what makes
    them real — v3.19.8 validates OOXML and ODF by opening the container and
    reading what it declares, so a fixture built this way exercises the real
    code path rather than a mock of it;
  * the sniffed formats are minimal-but-genuine byte sequences with the magic
    numbers libmagic actually keys on.

⚠️ **THREE EXTENSIONS HAVE NO FIXTURE AND THE REASON IS RECORDED, NOT THE
EXEMPTION.** `.doc`, `.xls` and `.ppt` are legacy OLE2 compound documents; a
valid one cannot be constructed without a builder library and this project has no
such dependency. They are listed in `NO_FIXTURE` **with the mechanism spelled
out**, because CLAUDE.md records that *an exemption is a claim about a mechanism,
not about a number* — an entry here says "this cannot be built", not "this fails
and we would rather it did not". They are still covered, one step weaker, by
`test_legacy_office_extensions_accept_the_generic_ole_types`, which pins the
mapping even though it cannot pin a file against it.
"""

import io
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from src.utils.file_validation import (
    ALLOWED_FILE_TYPES,
    BLOCKED_EXTENSIONS,
    OOXML_MARKERS,
    ODF_MIMETYPES,
    validate_uploaded_file,
)


# ---------------------------------------------------------------------------
# Fixture builders — real files, standard library only
# ---------------------------------------------------------------------------

def _ooxml(marker):
    """A minimal but structurally valid OOXML package."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            f'<Override PartName="/main.xml" ContentType="application/vnd.openxmlformats-officedocument.{marker}.main+xml"/>'
            '</Types>'
        )
        z.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships/>')
        z.writestr('main.xml', '<?xml version="1.0"?><document/>')
    return buf.getvalue()


def _odf(mimetype):
    """A minimal but structurally valid ODF package.

    The `mimetype` entry is written first and STORED (uncompressed), which is
    what the ODF spec requires and what makes the format identifiable without
    unpacking.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), mimetype, compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/manifest.xml', '<?xml version="1.0"?><manifest/>')
        z.writestr('content.xml', '<?xml version="1.0"?><document/>')
    return buf.getvalue()


def _plain_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('notes.txt', 'chapter documents\n')
    return buf.getvalue()


def _pdf():
    # A genuinely loadable one-page PDF. libmagic keys on the `%PDF` header;
    # the trailer is here so the fixture is a real file and not a header.
    return (
        b'%PDF-1.4\n'
        b'1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
        b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
        b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n'
        b'trailer<</Root 1 0 R>>\n%%EOF\n'
    )


#: A real 1x1 PNG (the smallest valid one), as bytes.
_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
    b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)

#: A real 1x1 JPEG.
_JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffff'
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffc2000b0801000100'
    '011100ffc40014000100000000000000000000000000000009ffda0008010100000000'
    '3fffd9'
)

#: A real 1x1 GIF.
_GIF = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04'
    b'\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D'
    b'\x01\x00;'
)


def _webp():
    body = b'VP8 \x0a\x00\x00\x00' + b'\x00' * 10
    return b'RIFF' + (len(body) + 4).to_bytes(4, 'little') + b'WEBP' + body


#: extension -> (bytes, human description)
FIXTURES = {
    '.pdf': _pdf(),
    '.docx': _ooxml('wordprocessingml.document'),
    '.xlsx': _ooxml('spreadsheetml.sheet'),
    '.pptx': _ooxml('presentationml.presentation'),
    '.odt': _odf('application/vnd.oasis.opendocument.text'),
    '.ods': _odf('application/vnd.oasis.opendocument.spreadsheet'),
    '.odp': _odf('application/vnd.oasis.opendocument.presentation'),
    '.zip': _plain_zip(),
    '.csv': b'name,hours\nMason,12\n',
    '.txt': b'A note from the doctor.\n',
    '.md': b'# Minutes\n\nThe chapter met.\n',
    '.log': b'2026-08-13 INFO deploy ok\n',
    '.json': b'{"hours": 12}\n',
    '.xml': b'<?xml version="1.0"?><minutes/>\n',
    '.rtf': b'{\\rtf1\\ansi Chapter minutes.}',
    '.jpg': _JPEG,
    '.jpeg': _JPEG,
    '.png': _PNG,
    '.gif': _GIF,
    '.webp': _webp(),
}

#: Extensions with no fixture, and the MECHANISM that prevents one.
NO_FIXTURE = {
    '.doc': 'legacy OLE2 compound document — cannot be constructed without a '
            'builder library, and this project has no such dependency',
    '.xls': 'legacy OLE2 compound document — as above',
    '.ppt': 'legacy OLE2 compound document — as above',
}


class EveryAllowedExtensionIsCoveredTests(SimpleTestCase):
    """The enumeration. This is the part that survives the next person."""

    def test_every_allowed_extension_has_a_fixture_or_a_stated_reason(self):
        """
        Adding a row to `ALLOWED_FILE_TYPES` without a sample file fails here.

        This is the guard that was missing. `ALLOWED_FILE_TYPES` is a claim about
        what real files look like, and a claim about the world needs a piece of
        the world to check it against.
        """
        covered = set(FIXTURES) | set(NO_FIXTURE)
        missing = sorted(set(ALLOWED_FILE_TYPES) - covered)
        self.assertEqual(
            missing, [],
            f'These extensions are accepted by the application but no fixture '
            f'proves a real one passes validation: {missing}. Add a builder to '
            f'FIXTURES, or an entry to NO_FIXTURE stating the mechanism that '
            f'prevents building one. Do not simply delete this assertion — the '
            f'last time this map went unchecked it rejected every spreadsheet '
            f'in the chapter for two days.'
        )

    def test_no_fixture_describes_extensions_that_are_actually_allowed(self):
        """A stale exemption is worse than none — it reads as considered."""
        stale = sorted(set(NO_FIXTURE) - set(ALLOWED_FILE_TYPES))
        self.assertEqual(stale, [], f'NO_FIXTURE names extensions that are no longer allowed: {stale}')

    def test_no_fixture_reasons_are_real_sentences(self):
        """
        An exemption is a claim about a MECHANISM, not about a number.

        A one-word reason is how "cannot be built" decays into "fails and we
        stopped looking".
        """
        for ext, reason in NO_FIXTURE.items():
            self.assertGreater(
                len(reason), 30,
                f'{ext} is exempted without saying why in enough words to be checkable',
            )

    def test_the_fixture_set_and_the_blocklist_do_not_overlap(self):
        """A fixture for a blocked extension would assert a contradiction."""
        overlap = sorted(set(FIXTURES) & BLOCKED_EXTENSIONS)
        self.assertEqual(overlap, [], f'fixtures exist for blocked extensions: {overlap}')


class RealFilesOfEveryAllowedTypeAreAcceptedTests(SimpleTestCase):
    """
    The regression this release exists for.

    Against the v3.19.7 tree, `.docx`, `.xlsx` and `.pptx` fail here.
    """

    def test_every_fixture_passes_validation(self):
        for ext, payload in sorted(FIXTURES.items()):
            with self.subTest(ext=ext):
                upload = SimpleUploadedFile(f'chapter-file{ext}', payload)
                try:
                    validate_uploaded_file(upload)
                except ValidationError as exc:
                    self.fail(
                        f'A real {ext} file was rejected as malicious: {exc.messages}. '
                        f'This is what members experience on all 17 upload paths.'
                    )

    def test_validation_leaves_the_file_pointer_at_the_start(self):
        """
        The caller saves the file straight after validating it.

        A validator that consumes the stream and does not rewind stores a
        truncated file, which is a data-loss bug wearing a security fix's
        clothes. `_validate_zip_container` rewinds in a `finally`, and so does
        the magic path since v3.19.8.
        """
        for ext in ('.pdf', '.docx', '.odt', '.zip', '.png'):
            with self.subTest(ext=ext):
                upload = SimpleUploadedFile(f'x{ext}', FIXTURES[ext])
                validate_uploaded_file(upload)
                self.assertEqual(
                    upload.tell(), 0,
                    f'{ext}: validation left the pointer at {upload.tell()}',
                )

    def test_legacy_office_extensions_accept_the_generic_ole_types(self):
        """
        The weaker half of the coverage for the three `NO_FIXTURE` extensions.

        It cannot check a file, so it checks the mapping: libmagic reports OLE2
        compound documents under several names depending on version and on how
        much of the internal structure it recognises, and if only the specific
        `application/msword` is listed then the same class of failure that hit
        `.xlsx` is waiting for `.doc`. Pinning the mapping is not as good as
        pinning a file. It is what is available, and saying so is the point.
        """
        generic = {'application/x-ole-storage', 'application/vnd.ms-office', 'application/CDFV2'}
        for ext in NO_FIXTURE:
            with self.subTest(ext=ext):
                allowed = set(ALLOWED_FILE_TYPES[ext])
                self.assertTrue(
                    allowed & generic,
                    f'{ext} lists only {sorted(allowed)}. A real legacy Office file '
                    f'is frequently reported as one of {sorted(generic)}, and no '
                    f'fixture exists to catch it if it is.',
                )


class ContentThatContradictsItsExtensionIsRejectedTests(SimpleTestCase):
    """
    The negative controls.

    Without these, widening the sniff window and adding the container path would
    be indistinguishable from deleting the check — which is the failure mode this
    release is reacting to, one direction over.
    """

    def test_html_wearing_a_document_extension_is_rejected(self):
        html = b'<!DOCTYPE html><html><body><form action="/login/"></form></body></html>'
        for ext in ('.pdf', '.png', '.docx', '.odt', '.zip'):
            with self.subTest(ext=ext):
                with self.assertRaises(ValidationError):
                    validate_uploaded_file(SimpleUploadedFile(f'gpa{ext}', html))

    def test_a_zip_backed_extension_must_actually_be_a_zip(self):
        with self.assertRaises(ValidationError):
            validate_uploaded_file(SimpleUploadedFile('transcript.xlsx', b'not a zip at all'))

    def test_the_wrong_ooxml_inside_the_right_container_is_rejected(self):
        """
        The precise thing the structural check buys over `application/zip`.

        A Word document renamed `.xlsx` is a valid zip with a valid manifest, so
        every check that stops at "is it a zip" passes it. This one reads the
        manifest.
        """
        word = _ooxml('wordprocessingml.document')
        with self.assertRaises(ValidationError):
            validate_uploaded_file(SimpleUploadedFile('budget.xlsx', word))

    def test_the_wrong_odf_mimetype_is_rejected(self):
        text = _odf('application/vnd.oasis.opendocument.text')
        with self.assertRaises(ValidationError):
            validate_uploaded_file(SimpleUploadedFile('budget.ods', text))

    def test_every_container_extension_is_actually_routed_to_the_container_check(self):
        """
        Guards the dispatch, not the checkers.

        If an extension is added to `OOXML_MARKERS` but the `if` in
        `validate_mime_type` is not updated, the file silently goes back to
        libmagic and back to being rejected — the original bug, restored, in a
        release that exists to fix it.
        """
        for ext in list(OOXML_MARKERS) + list(ODF_MIMETYPES) + ['.zip']:
            with self.subTest(ext=ext):
                with self.assertRaises(ValidationError):
                    validate_uploaded_file(SimpleUploadedFile(f'x{ext}', b'plain text, not a container'))
