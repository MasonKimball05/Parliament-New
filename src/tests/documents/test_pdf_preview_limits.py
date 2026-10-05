"""
v3.44.4 — the in-page PDF preview must not render a page at whatever size
the PDF claims to be.

WHY THIS EXISTS. On 10-04-26 one 5-page, 4 MB scanned PDF took production
down for most of an hour. `convert_pdf_to_images` rendered each page at 150 dpi
of the page's declared size, and a scan commonly declares its page in
pixels-as-points (2550 x 3300 pt). That is a 5,300 x 6,900 pixel bitmap per
page. The process passed 570 MB, went into swap, and because Daphne runs every
ordinary view on one shared thread, no other page was served until someone
killed it. Restarting only helped until the next person opened the document.

The second test class covers a bug found while fixing that: the function read
`len(doc)` after `doc.close()`, which raises in current PyMuPDF, so every
conversion did all of its work and then returned None.
"""

import base64
import tempfile
from pathlib import Path

import fitz
from django.test import SimpleTestCase

from src.view import view_document
from src.view.view_document import convert_pdf_to_images


def _make_pdf(directory, name, page_sizes):
    path = str(Path(directory) / name)
    doc = fitz.open()
    for width, height in page_sizes:
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), 'Page text')
    doc.save(path)
    doc.close()
    return path


class PdfPreviewPixelCapTests(SimpleTestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_a_letter_page_is_still_rendered_at_150_dpi(self):
        """The control: an ordinary page is under the cap and is not shrunk."""
        result = convert_pdf_to_images(_make_pdf(self.dir, 'letter.pdf', [(612, 792)]))
        image = result['images'][0]
        self.assertEqual((image['width'], image['height']), (1275, 1650))

    def test_a_scan_that_declares_a_huge_page_is_capped(self):
        result = convert_pdf_to_images(_make_pdf(self.dir, 'scan.pdf', [(2550, 3300)] * 2))
        self.assertEqual(len(result['images']), 2)
        for image in result['images']:
            self.assertLessEqual(image['width'], view_document.PDF_PREVIEW_MAX_WIDTH_PX)
            self.assertLessEqual(image['height'], view_document.PDF_PREVIEW_MAX_HEIGHT_PX)
            # Without the cap this is 5313 x 6875.
            self.assertGreater(image['width'], 1000)

    def test_a_very_tall_page_is_capped_by_height(self):
        result = convert_pdf_to_images(_make_pdf(self.dir, 'tall.pdf', [(300, 14000)]))
        image = result['images'][0]
        self.assertLessEqual(image['height'], view_document.PDF_PREVIEW_MAX_HEIGHT_PX)

    def test_the_cap_has_not_been_raised_without_reading_the_note(self):
        """
        A 1400 x 2000 page is 8.4 MB raw. Raising these is a memory decision
        for a small server, not a picture-quality one.
        """
        self.assertLessEqual(view_document.PDF_PREVIEW_MAX_WIDTH_PX, 1600)
        self.assertLessEqual(view_document.PDF_PREVIEW_MAX_HEIGHT_PX, 2400)

    def test_images_are_jpeg_and_say_so(self):
        result = convert_pdf_to_images(_make_pdf(self.dir, 'one.pdf', [(612, 792)]))
        image = result['images'][0]
        self.assertEqual(image['mime'], 'image/jpeg')
        self.assertEqual(base64.b64decode(image['data'])[:3], b'\xff\xd8\xff')

    def test_the_template_uses_the_mime_type_it_is_given(self):
        from django.conf import settings
        template = (Path(settings.BASE_DIR) / 'templates' / 'view_document.html').read_text(encoding='utf-8')
        self.assertIn('data:{{ page.mime', template)
        self.assertNotIn('data:image/png;base64,{{ page.data }}', template)


class PdfPreviewReturnsItsResultTests(SimpleTestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_a_successful_conversion_is_returned(self):
        result = convert_pdf_to_images(_make_pdf(self.dir, 'three.pdf', [(612, 792)] * 3))
        self.assertIsNotNone(result)
        self.assertEqual(result['total_pages'], 3)
        self.assertFalse(result['truncated'])
        self.assertEqual([i['page'] for i in result['images']], [1, 2, 3])

    def test_a_long_pdf_is_truncated_and_says_how_long_it_is(self):
        path = _make_pdf(self.dir, 'long.pdf', [(612, 792)] * 4)
        result = convert_pdf_to_images(path, max_pages=2)
        self.assertEqual(len(result['images']), 2)
        self.assertEqual(result['total_pages'], 4)
        self.assertTrue(result['truncated'])

    def test_an_unreadable_file_returns_none(self):
        path = Path(self.dir) / 'broken.pdf'
        path.write_bytes(b'this is not a pdf')
        with self.assertLogs('src.view.view_document', level='ERROR'):
            self.assertIsNone(convert_pdf_to_images(str(path)))
