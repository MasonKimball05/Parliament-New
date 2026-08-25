"""
v3.19.9 — a malformed Office document must be REJECTED, not 500.

WHAT WAS WRONG
--------------
v3.19.8 replaced libmagic sniffing for the six zip-backed document formats with
a structural check: open the archive, read what the file declares about itself.
That is the right design and its docstring argued the error handling explicitly —

    "THIS RAISES ITS OWN VERDICT AND CATCHES NOTHING BROAD, deliberately …
     the detection cannot fail in a way that should be tolerated"

— and then wrapped `(zipfile.BadZipFile, OSError, EOFError)` around the
`ZipFile()` **constructor only**, leaving `archive.open(...)` and `fh.read(...)`
four lines below outside it. Measured against the real `validate_uploaded_file`
on 08-15-26, four ordinary malformations escaped as uncaught exceptions:

    unsupported compression method   NotImplementedError
    encrypted zip entry              RuntimeError
    corrupt member (bad CRC)         zipfile.BadZipFile   ← the same type the
                                                            constructor handler
                                                            already names
    encrypted ODF `mimetype`         RuntimeError

`validate_uploaded_file` is called from **17 upload sites** — excuse documents
(doctors' notes), legislation, committee and chapter documents, Kai attachments,
slating files, service-hours evidence — and none of them catches anything but
`ValidationError`. So each of those four is an unhandled 500 that any
authenticated member can produce by uploading a file, plus an error-log entry
and no message telling them what went wrong.

Note the third row. Enumerating exception types got the *type* right and the
*place* wrong: `BadZipFile` was handled at the constructor and unhandled at the
read. This is the eighth instance of CLAUDE.md's "a rule stated correctly, a
helper written to enforce it, then something left outside the helper", and the
first where the thing left outside was the second half of the same operation.

> **THE RULE: a `try` narrowed to what is allowed to fail has to be narrowed
> around the WHOLE of what is allowed to fail.** v3.19.7 was right that the
> verdict must live outside the `try`; v3.19.8 read that as "make the `try`
> small" and made it smaller than the detection. The structural fix is to put
> the detection in its own function that contains no verdict at all — then the
> catch can be as broad as a stdlib parser deserves, because there is nothing
> in scope for it to swallow.

WHY THE FIXTURES ARE BUILT BY HAND AND NOT COMMITTED
----------------------------------------------------
Same reasoning as `test_upload_type_fixtures.py`: standard library only, no
binary blobs in the repo, and every byte of the malformation is visible in this
file. Each helper patches one field of the zip format, so a reader can check
that the file is malformed in the way the test name claims and in no other.
"""

import io
import zipfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from src.utils.file_validation import validate_uploaded_file

_OOXML_CONTENT_TYPE = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)


def _docx_bytes(compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression) as archive:
        archive.writestr('[Content_Types].xml', _OOXML_CONTENT_TYPE)
        archive.writestr('word/document.xml', '<w:document/>' * 50)
    return buf.getvalue()


def _odt_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
    return buf.getvalue()


def _set_compression_method(raw, method):
    """Claim a compression method `zipfile` cannot decompress (98 = not supported)."""
    data = bytearray(raw)
    for signature, offset in ((b'PK\x03\x04', 8), (b'PK\x01\x02', 10)):
        i = 0
        while True:
            i = data.find(signature, i)
            if i < 0:
                break
            data[i + offset:i + offset + 2] = method.to_bytes(2, 'little')
            i += 4
    return bytes(data)


def _set_encrypted_flag(raw):
    """Set bit 0 of the general-purpose flag: 'this entry is password-protected'."""
    data = bytearray(raw)
    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        i = data.find(signature)
        while i >= 0:
            data[i + offset] |= 0x01
            i = data.find(signature, i + 4)
    return bytes(data)


def _corrupt_stored_member(raw, needle, replacement):
    """Change stored bytes without fixing the CRC, so the read fails, not the open."""
    data = bytearray(raw)
    i = data.find(needle)
    assert i > 0, 'fixture is not shaped the way this helper assumes'
    data[i:i + len(replacement)] = replacement
    return bytes(data)


class MalformedArchivesAreRejectedNotRaisedTests(SimpleTestCase):
    """
    Every case here raised an uncaught exception before v3.19.9. The assertion
    is `ValidationError` specifically — `assertRaises(Exception)` would pass
    against the broken code and prove nothing.
    """

    def _assert_rejected(self, name, data):
        with self.assertRaises(ValidationError) as ctx:
            validate_uploaded_file(SimpleUploadedFile(name, data))
        return str(ctx.exception)

    def test_the_control_a_well_formed_docx_is_still_accepted(self):
        """
        FIRST, because a validator that rejected everything would pass every
        other test in this class and break every Word upload in the chapter —
        which is the regression v3.19.8 was itself written to fix.
        """
        self.assertTrue(validate_uploaded_file(
            SimpleUploadedFile('minutes.docx', _docx_bytes())))

    def test_an_unsupported_compression_method_is_rejected(self):
        """Was: NotImplementedError. Real files hit this — deflate64 from some
        Windows archivers is a compression method `zipfile` will not decode."""
        self._assert_rejected('minutes.docx', _set_compression_method(_docx_bytes(), 98))

    def test_a_password_protected_entry_is_rejected(self):
        """Was: RuntimeError('… is encrypted, password required for extraction')."""
        self._assert_rejected('minutes.docx', _set_encrypted_flag(_docx_bytes()))

    def test_a_corrupt_member_is_rejected(self):
        """
        Was: BadZipFile('Bad CRC-32') — from the read, while the SAME exception
        type was handled at the open. A truncated-then-repaired upload, or a
        file recovered off a failing disk, gets here.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('[Content_Types].xml', _OOXML_CONTENT_TYPE + 'A' * 300)
        corrupt = _corrupt_stored_member(buf.getvalue(), b'A' * 300, b'B' * 32)
        self._assert_rejected('minutes.docx', corrupt)

    def test_an_encrypted_odf_mimetype_entry_is_rejected(self):
        """The ODF branch had the same gap, reached through a different entry."""
        self._assert_rejected('notes.odt', _set_encrypted_flag(_odt_bytes()))

    def test_a_file_that_is_not_a_zip_at_all_is_still_rejected(self):
        """The one case v3.19.8 did handle. Kept so the fix cannot lose it."""
        self._assert_rejected('minutes.docx', b'this is not a zip archive, at all')

    def test_a_docx_containing_a_spreadsheet_manifest_is_rejected(self):
        """
        The check's actual purpose, asserted alongside the error handling: it is
        not enough to be a readable zip, it has to be the document it claims.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('[Content_Types].xml', _OOXML_CONTENT_TYPE.replace(
                'wordprocessingml.document', 'spreadsheetml.sheet'))
        self._assert_rejected('minutes.docx', buf.getvalue())


class ARejectionIsStillAMessageToAMemberTests(SimpleTestCase):

    def test_the_rejection_names_the_extension_and_does_not_leak_internals(self):
        """
        A 500 tells a member nothing; a `ValidationError` is rendered to them by
        `messages.error(request, str(e))` at every call site. So the text is
        part of the fix, not decoration — and it must not paste a stdlib
        exception repr into the page.
        """
        with self.assertRaises(ValidationError) as ctx:
            validate_uploaded_file(SimpleUploadedFile(
                'minutes.docx', _set_encrypted_flag(_docx_bytes())))
        message = str(ctx.exception)
        self.assertIn('.docx', message)
        self.assertNotIn('RuntimeError', message)
        self.assertNotIn('Traceback', message)


class ValidationStillLeavesTheFileReadableTests(SimpleTestCase):
    """
    v3.19.8 found that a validator which consumes the stream stores a truncated
    document, and covered the success path. The REJECTION path needs the same
    guarantee for a different reason: several call sites catch the
    `ValidationError`, drop the file and keep the record, and one of them
    re-reads the upload afterwards. A validator that leaves the pointer mid-file
    on the way out is a data-loss bug on the error path — the path nobody
    exercises.
    """

    def test_the_pointer_is_rewound_after_a_rejection(self):
        upload = SimpleUploadedFile('minutes.docx', _set_encrypted_flag(_docx_bytes()))
        with self.assertRaises(ValidationError):
            validate_uploaded_file(upload)
        self.assertEqual(upload.tell(), 0)

    def test_the_pointer_is_rewound_after_a_success(self):
        upload = SimpleUploadedFile('minutes.docx', _docx_bytes())
        validate_uploaded_file(upload)
        self.assertEqual(upload.tell(), 0)
        self.assertEqual(upload.read(2), b'PK', 'the whole file must still be readable')
