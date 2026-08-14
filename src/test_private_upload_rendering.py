"""
v3.19.7 — what a private upload is allowed to BECOME in the browser.

THE FINDING THIS MODULE EXISTS FOR
----------------------------------
v3.19.6 closed `/media/` for eight upload directories and built eight
ownership-aware views to serve them instead. It got the authorisation right —
each view re-uses the predicate its host page applies, and the 08-11 review went
looking for a hole in that and did not find one.

What it did not decide is what happens AFTER the check passes.
`_stream_private_file` sent every file with `as_attachment=False` and a content
type from `mimetypes.guess_type`, i.e. from the stored filename. So a file named
`x.html` was served as `text/html`, rendered, and became a page on this origin —
in the session of a Kai reviewer, a slating committee member or the VPP, because
those are exactly the people these eight views serve.

And four of the eight upload paths validated nothing, so getting `x.html` into
one of those directories took no exploit at all:

    SlatingApplication.gpa_screenshot      — checked the uploader's OWN
                                             `content_type` multipart header
    KaiReportFieldResponse.file_value      — nothing
    SlatingApplicationResponse.file_value  — nothing
    ServiceFieldResponse.file_value        — nothing

THREE LAYERS, AND THE TESTS BELOW ARE GROUPED BY THEM
----------------------------------------------------
1. **Serving** (`INLINE_SAFE_CONTENT_TYPES`) — the mitigation. Render PDFs and
   raster images; download everything else. This holds even if layers 2 and 3
   both fail, which is why it is first.
2. **Storage** (`_reject_browser_executable`) — the layer no writer can forget,
   because every upload in the application funnels through
   `Storage.get_valid_name`.
3. **Validation** (`validate_uploaded_file` at the four writers) — the real
   check: extension allowlist, size, and a MIME sniff that must agree with the
   extension.

⚠️ THE POINT OF TESTING ALL THREE SEPARATELY. A single end-to-end test ("a
member cannot upload an HTML file") would pass with any ONE of the three layers
working and would go quiet the moment someone removed the other two — reporting
the same green while the defence in depth silently became defence in one. Each
class below asserts its layer in isolation, so removing a layer fails a test
that names it.
"""
import os
import tempfile

from django.core.exceptions import SuspiciousFileOperation, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings

from src.storage import SanitizedFileSystemStorage
from src.utils.file_validation import BLOCKED_EXTENSIONS, validate_uploaded_file
from src.view.serve_private_upload import (
    INLINE_SAFE_CONTENT_TYPES, _stream_private_file,
)


class _FakeFieldFile:
    """
    The narrowest thing `_stream_private_file` accepts: truthy, with a `.path`.

    Deliberately not a real `FieldFile` — building one needs a model instance, a
    storage and a saved row, none of which this test is about. The helper takes
    a `FieldFile` and performs no lookups precisely so it can be exercised like
    this.
    """

    def __init__(self, path):
        self.path = path

    def __bool__(self):
        return True


class InlineRenderingIsAnAllowlistTests(SimpleTestCase):
    """Layer 1 — the mitigation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: None)

    def _serve(self, filename, body=b'x'):
        path = os.path.join(self.tmp, filename)
        with open(path, 'wb') as handle:
            handle.write(body)
        with override_settings(MEDIA_ROOT=self.tmp):
            response = _stream_private_file(_FakeFieldFile(path))
        response.close()
        return response

    def test_html_is_downloaded_and_not_rendered(self):
        """
        The finding, reduced to one assertion. Before v3.19.7 this was
        `inline; filename="notes.html"` — a page on am-parliament.org authored
        by whoever uploaded it.
        """
        response = self._serve('notes.html', b'<html><body>hi</body></html>')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_svg_is_downloaded_even_though_it_is_an_image(self):
        """
        ⚠️ The one that a `startswith('image/')` check would get wrong, and the
        reason `INLINE_SAFE_CONTENT_TYPES` enumerates instead of pattern-matching.
        An SVG is an XML document that may contain `<script>`; it is an image
        everywhere except in the way that matters here.
        """
        response = self._serve('chart.svg', b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertEqual(response['Content-Type'], 'image/svg+xml')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_javascript_is_downloaded(self):
        """
        The second half of the CSP bypass: `script-src 'self'` permits
        `<script src="/kai/reports/responses/<id>/file/">`, so a `.js` served
        from one of these routes is a same-origin script the policy allows.
        """
        response = self._serve('payload.js', b'alert(1)')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_pdf_still_renders_because_reviewers_preview_them(self):
        """The negative control. If this fails the fix has broken the feature."""
        response = self._serve('evidence.pdf', b'%PDF-1.4 ...')
        self.assertIn('inline', response['Content-Disposition'])

    def test_png_still_renders_because_the_bug_screenshot_is_an_img_tag(self):
        """
        `templates/bug_report_detail.html` renders the screenshot as
        `<img src="{% url 'bug_report_screenshot' … %}">`. Browsers ignore
        `Content-Disposition` on subresource loads, so `attachment` would not
        actually break the tag — but serving an image as a download is still
        wrong, and this records that images are inline ON PURPOSE rather than by
        an accident of the allowlist.
        """
        response = self._serve('screenshot.png', b'\x89PNG\r\n\x1a\n')
        self.assertIn('inline', response['Content-Disposition'])

    def test_an_unknown_type_is_downloaded(self):
        """
        `mimetypes.guess_type` returns None for an unrecognised extension and
        the helper falls back to `application/octet-stream`. **A type we could
        not identify is a type nobody has reasoned about**, so it must not
        render. This is the assertion that keeps the allowlist an allowlist.
        """
        response = self._serve('mystery.qqq')
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment', response['Content-Disposition'])

    def test_nosniff_is_set_on_the_response_itself(self):
        """
        `SECURE_CONTENT_TYPE_NOSNIFF` sets this globally through
        `SecurityMiddleware`, and this helper sets it again locally. Stated
        twice deliberately: this is the one response in the application whose
        BODY is member-supplied and whose TYPE is guessed from a member-supplied
        name, and a global setting can be switched off for a reason that has
        nothing to do with this file.
        """
        response = self._serve('notes.html', b'<html></html>')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_the_inline_set_contains_no_markup_type(self):
        """
        The property, not the instance — the guard against a future addition.
        Anything that is `text/*`, or XML, or SVG, is a document the browser
        parses, and no such type belongs in the inline set however convenient it
        looks at the time.
        """
        for content_type in INLINE_SAFE_CONTENT_TYPES:
            self.assertFalse(
                content_type.startswith('text/')
                or 'xml' in content_type
                or 'html' in content_type,
                f'{content_type} is parsed as a document by the browser and '
                f'must not be rendered inline from this origin.',
            )


class StorageRefusesBrowserExecutableNamesTests(SimpleTestCase):
    """
    Layer 2 — the one no writer can forget.

    ⚠️ Every one of these would have been caught by validation IF the writer had
    called it. Four did not, and nothing distinguished them from the four that
    did: a view assigning `request.FILES[...]` to a model field looks identical
    whether or not it validated first. That is why this layer sits at the
    storage funnel rather than being a fifth thing to remember.
    """

    def setUp(self):
        self.storage = SanitizedFileSystemStorage()

    def test_html_cannot_be_stored_at_all(self):
        with self.assertRaises(SuspiciousFileOperation):
            self.storage.get_valid_name('doctors note.html')

    def test_svg_cannot_be_stored(self):
        with self.assertRaises(SuspiciousFileOperation):
            self.storage.get_valid_name('transcript.svg')

    def test_the_check_survives_the_slugifier(self):
        """
        Ordering matters and is easy to get backwards. `get_valid_name`
        slugifies the stem and strips non-alphanumerics from the extension, so
        the check has to run on the CLEANED name — a file called
        `report .HTML ` reaches the blocklist as `.html` only after cleaning.
        Checking the raw input would miss every spelling the sanitiser
        normalises.
        """
        with self.assertRaises(SuspiciousFileOperation):
            self.storage.get_valid_name('report .HTML')

    def test_ordinary_uploads_are_untouched(self):
        """
        The negative control, and the reason this layer is a blocklist rather
        than the allowlist from `file_validation`: songbook audio is a real
        feature and `.mp3` is not in `ALLOWED_FILE_TYPES`. An allowlist here
        would break shipping features for no gain, because the per-view
        validation already applies one where it belongs.
        """
        self.assertEqual(self.storage.get_valid_name('Doctors Note.pdf'), 'doctors-note.pdf')
        self.assertEqual(self.storage.get_valid_name('Chapter Song.mp3'), 'chapter-song.mp3')

    def test_the_blocklist_names_what_the_browser_runs(self):
        """
        v3.19.7 — the blocklist used to name what runs on a SERVER (`.php`,
        `.jsp`, `.exe`) and nothing that runs in a BROWSER, which is this
        application's actual exposure: it never executes an upload, it serves
        uploads back from its own origin.
        """
        for ext in ('.html', '.htm', '.xhtml', '.shtml', '.svg', '.svgz', '.xsl', '.js'):
            self.assertIn(ext, BLOCKED_EXTENSIONS)


class MimeValidationActuallyRejectsTests(SimpleTestCase):
    """
    Layer 3 — and a bug found while wiring the four writers into it.

    ⚠️ `validate_mime_type` RAISED ITS ValidationError INSIDE A `try` WHOSE
    `except Exception` DOWNGRADED IT TO A LOG LINE. The function's entire
    purpose — catch a file whose content disagrees with its extension — was
    cancelled by its own error handling, and every caller believed it was
    getting a content check. The broad catch is still right for what it was
    written for (python-magic needs libmagic, and a missing system library must
    not take out every upload form), so the `try` now covers only the detection
    call and the verdict is reached outside it.

    **The general form: a `try` containing both the detection and the verdict
    cannot fail open on one without failing open on the other.**
    """

    def test_content_that_disagrees_with_the_extension_is_rejected(self):
        spoofed = SimpleUploadedFile(
            'transcript.pdf',
            b'<html><body>not a pdf</body></html>',
            content_type='application/pdf',
        )
        with self.assertRaises(ValidationError):
            validate_uploaded_file(spoofed)

    def test_a_real_pdf_passes(self):
        """
        The negative control. Without it the test above passes against a
        `validate_uploaded_file` that rejects everything, which is a fix nobody
        would notice until the first real upload.
        """
        real = SimpleUploadedFile(
            'minutes.pdf',
            b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n',
            content_type='application/pdf',
        )
        validate_uploaded_file(real)  # must not raise

    def test_the_declared_content_type_is_not_evidence(self):
        """
        The `gpa_screenshot` bug in one assertion. `UploadedFile.content_type`
        is the multipart part header — sent by the uploader, never measured — so
        a file can claim to be a PNG and be anything at all. The old check was
        `if f.content_type not in [image/*, pdf]`, and this file passes it.
        """
        liar = SimpleUploadedFile(
            'screenshot.png',
            b'<html><body>not a png</body></html>',
            content_type='image/png',
        )
        self.assertEqual(liar.content_type, 'image/png')
        with self.assertRaises(ValidationError):
            validate_uploaded_file(liar)


class TheFourWritersValidateTests(TestCase):
    """
    Layer 3, at the call sites — asserted structurally rather than by driving
    four multi-step forms.

    ⚠️ WHY AN AST WALK AND NOT FOUR POST REQUESTS. Each of these writers sits
    behind a period that must be open, a form field that must be configured, and
    a feature flag; a functional test for each would be four long fixtures whose
    failure mode is "the fixture broke". The property that actually matters is
    cheap and exact: **the value assigned into an upload field must be a value
    the same function validated.**

    ⚠️ AND THE FIRST DRAFT OF THIS TEST WAS THE WEAKER PROPERTY — "the function
    mentions the validator somewhere" — WHICH FOUND ONLY THREE OF THE FOUR.
    `submit_kai_report` contains a `validate_uploaded_file` call on
    `request.FILES['supporting_document']`, a field name the Kai form does not
    have, left from an earlier version: dead code that made the function look
    validated while its custom-field write went through unchecked. Run against
    the pristine v3.19.6 tree the first draft reported 3 offenders; this version
    reports 4.

    **That is this repo's own rule arriving from a new direction: a test that
    greps for the presence of the right call cannot tell the right call from a
    call on the wrong object.** Match the VALUE, not the vocabulary.
    """

    WRITER_MODULES = (
        'src/view/kai_reports.py',
        'src/view/slating/apply.py',
        'src/view/service_user_dashboard.py',
    )

    VALIDATORS = {'validate_uploaded_file', '_validated_upload'}
    UPLOAD_FIELDS = {'file_value', 'gpa_screenshot'}

    def _upload_writes(self, func):
        """Yield the assigned value node for every write into an upload field."""
        import ast

        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                name = None
                if isinstance(target, ast.Attribute):
                    name = target.attr
                elif isinstance(target, ast.Subscript) and isinstance(
                        getattr(target, 'slice', None), ast.Constant):
                    name = target.slice.value
                if isinstance(name, str) and name in self.UPLOAD_FIELDS:
                    yield node.value

    def _validated_names(self, func):
        """Names passed as the file argument to a validator inside `func`."""
        import ast

        validated = set()
        for node in ast.walk(func):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            fname = getattr(callee, 'id', None) or getattr(callee, 'attr', None)
            if fname not in self.VALIDATORS:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    validated.add(arg.id)
        return validated

    def test_every_upload_field_write_assigns_a_validated_value(self):
        import ast
        import pathlib

        from django.conf import settings

        base = pathlib.Path(settings.BASE_DIR)
        offenders = []

        for rel in self.WRITER_MODULES:
            tree = ast.parse((base / rel).read_text())

            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if 'FILES' not in ast.dump(func):
                    continue

                validated = self._validated_names(func)

                for value in self._upload_writes(func):
                    # `x.file_value = _validated_upload(...)` — the validation
                    # IS the assigned expression.
                    if isinstance(value, ast.Call):
                        callee = value.func
                        fname = (getattr(callee, 'id', None)
                                 or getattr(callee, 'attr', None))
                        if fname in self.VALIDATORS:
                            continue
                    if isinstance(value, ast.Name) and value.id in validated:
                        continue
                    offenders.append(
                        f'{rel}::{func.name} (line {value.lineno})')

        self.assertEqual(
            offenders, [],
            'These writes put an uploaded file into a private upload field '
            'without validating THAT value. Route it through '
            '`validate_uploaded_file` (or the module helper), the way the '
            'writers beside them already do:\n  ' + '\n  '.join(offenders),
        )
