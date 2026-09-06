"""
09-06-26 — Mason: "The docx page handler, on desktop, does not show images
from the document. Can we add that so it will show images instead of just
the text and whatnot?"

## Root cause

`convert_docx_to_html()` (`src/view/view_document.py`) never had an "images
are disabled" switch to flip. mammoth's default `convert_image` — used
whenever no custom one is passed, which this codebase never did — is
`mammoth.images.data_uri`, which already embeds every image in the docx as
`<img src="data:{content_type};base64,{data}">`. The bug was one step later:
`bleach.clean()`'s default `protocols` allowlist is only
`{http, https, mailto}`, and `data:` isn't in it. bleach treats `src` as a
URI attribute regardless of tag, so it silently dropped the `src` off every
`<img>` during sanitization — the tag itself survived (`img` is in
`_DOCX_ALLOWED_TAGS`, `src` is in `_DOCX_ALLOWED_ATTRS['img']`), just with
nothing left to render. That's why images looked like they were being
stripped even though nothing in this file's allowlists was ever explicitly
blocking them.

## Fix

`_DOCX_ALLOWED_PROTOCOLS` widens bleach's protocol allowlist to include
`data`. Because bleach's protocol check isn't scoped per-tag/attribute — the
same allowlist governs `img[src]` and `a[href]` alike — widening it also lets
a `data:` URI survive in a link, which is a real (if narrow) opening: a
`data:text/html;base64,<script>...` href would previously have been stripped
outright. `_sanitize_docx_html_images()` runs immediately after
`bleach.clean()` to close that back up: it strips `data:` out of any `href`
unconditionally (a link never legitimately needs to be a data URI here) and
only lets an `img[src]` data URI through if it actually matches
`data:image/<type>;base64,<data>` — so a docx image part with a
relabeled/malformed content-type can't turn its data URI into anything else
Chrome/Safari would try to interpret as non-image content.
"""
import base64
import io
import re

from django.conf import settings
from django.test import SimpleTestCase

from src.view.view_document import (
    convert_docx_to_html,
    _sanitize_docx_html_images,
    _DOCX_ALLOWED_PROTOCOLS,
)

# A minimal, valid 1x1 red PNG, hand-built rather than pulled in via Pillow —
# keeps this test file dependency-free.
_TINY_PNG_BASE64 = (
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
    '+A8AAQUBAScY42YAAAAASUVORK5CYII='
)


def _build_docx_with_image(tmp_path):
    """
    Builds a real, minimal .docx (a docx is a zip of OOXML parts) containing
    one embedded PNG, without depending on python-docx being installed in
    the app's own environment — this test only needs a package mammoth can
    read, not a full python-docx round trip.
    """
    import zipfile

    png_bytes = base64.b64decode(_TINY_PNG_BASE64)
    docx_path = tmp_path / 'test_with_image.docx'

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
        '</Relationships>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<w:body>'
        '<w:p><w:r><w:t>Hello world, this is a test document.</w:t></w:r></w:p>'
        '<w:p><w:r><w:drawing><wp:inline>'
        '<wp:extent cx="914400" cy="914400"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="image1.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        '</a:graphicData></a:graphic>'
        '</wp:inline></w:drawing></w:r></w:p>'
        '<w:p><w:r><w:t>Some text after the image.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )

    with zipfile.ZipFile(docx_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', content_types)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('word/document.xml', document_xml)
        z.writestr('word/_rels/document.xml.rels', document_rels)
        z.writestr('word/media/image1.png', png_bytes)

    return docx_path


class ConvertDocxToHtmlIncludesImagesTests(SimpleTestCase):
    """
    End-to-end: a real .docx, run through the actual mammoth+bleach
    pipeline this app uses, not a mocked-out version of it.
    """

    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def test_the_image_survives_the_full_pipeline(self):
        docx_path = _build_docx_with_image(self.tmp_path)
        html = convert_docx_to_html(str(docx_path))
        self.assertIsNotNone(html, 'convert_docx_to_html returned None — check mammoth is installed')
        self.assertIn('<img', html)
        self.assertIn(f'src="data:image/png;base64,{_TINY_PNG_BASE64}"', html)

    def test_the_surrounding_text_is_still_there(self):
        docx_path = _build_docx_with_image(self.tmp_path)
        html = convert_docx_to_html(str(docx_path))
        self.assertIn('Hello world, this is a test document.', html)
        self.assertIn('Some text after the image.', html)

    def test_negative_control_without_the_widened_protocol_list_the_image_is_lost(self):
        """
        Confirms the bug this fix closes actually reproduces on this exact
        fixture — proves the fixture is capable of catching a regression,
        not just capable of passing.
        """
        import bleach
        docx_path = _build_docx_with_image(self.tmp_path)
        import mammoth
        with open(docx_path, 'rb') as f:
            raw_html = mammoth.convert_to_html(f).value
        self.assertIn('data:image/png;base64,', raw_html, 'fixture sanity check: mammoth should have embedded the image')

        pre_fix_cleaned = bleach.clean(
            raw_html,
            tags=['p', 'img'],
            attributes={'img': ['src', 'alt', 'width', 'height']},
            strip=True,
        )
        self.assertIn('<img', pre_fix_cleaned)
        self.assertNotIn('src=', pre_fix_cleaned, 'pre-fix bleach defaults should strip the src attribute entirely')


class DocxImageProtocolIsScopedToImagesOnlyTests(SimpleTestCase):
    """
    The security half: widening bleach's protocol allowlist to let images
    through must not also let a data: URI survive as a link.
    """

    def test_data_is_in_the_widened_allowlist(self):
        self.assertIn('data', _DOCX_ALLOWED_PROTOCOLS)

    def test_the_original_safe_protocols_are_still_allowed(self):
        for protocol in ('http', 'https', 'mailto'):
            self.assertIn(protocol, _DOCX_ALLOWED_PROTOCOLS)

    def test_a_data_uri_href_is_stripped(self):
        malicious = (
            '<a href="data:text/html;base64,'
            'PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">click</a>'
        )
        cleaned = _sanitize_docx_html_images(malicious)
        self.assertNotIn('href=', cleaned)
        self.assertIn('click', cleaned, 'link text should survive even though the href is stripped')

    def test_an_ordinary_http_href_is_untouched(self):
        html = '<a href="https://example.com">click</a>'
        self.assertEqual(_sanitize_docx_html_images(html), html)

    def test_a_valid_image_data_uri_src_is_kept(self):
        html = f'<img src="data:image/png;base64,{_TINY_PNG_BASE64}" alt="test">'
        self.assertEqual(_sanitize_docx_html_images(html), html)

    def test_an_image_src_with_a_relabeled_content_type_is_stripped(self):
        """
        Guards against a docx image PART whose declared content-type has
        been changed to something other than an image type — the src
        should be dropped (falling back to alt text) rather than trusted
        just because it arrived via an <img> tag.
        """
        malicious = (
            '<img src="data:text/html;base64,'
            'PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==" alt="test">'
        )
        cleaned = _sanitize_docx_html_images(malicious)
        self.assertNotIn('src=', cleaned)
        self.assertIn('alt="test"', cleaned)

    def test_end_to_end_the_full_pipeline_also_blocks_a_data_uri_link(self):
        """
        Same guarantee as the unit-level tests above, exercised through the
        real bleach.clean() + _sanitize_docx_html_images() call sequence
        used by convert_docx_to_html(), not just the second half alone.
        """
        import bleach
        from src.view.view_document import _DOCX_ALLOWED_TAGS, _DOCX_ALLOWED_ATTRS

        malicious_html = (
            '<p>hi</p>'
            '<a href="data:text/html;base64,'
            'PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">click</a>'
        )
        cleaned = bleach.clean(
            malicious_html,
            tags=_DOCX_ALLOWED_TAGS,
            attributes=_DOCX_ALLOWED_ATTRS,
            protocols=_DOCX_ALLOWED_PROTOCOLS,
            strip=True,
        )
        cleaned = _sanitize_docx_html_images(cleaned)
        self.assertNotIn('data:text/html', cleaned)
        self.assertNotIn('<script', cleaned)
