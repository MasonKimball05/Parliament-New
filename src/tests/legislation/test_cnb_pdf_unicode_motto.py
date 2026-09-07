"""
v3.29.21 — Mason reported: "The pdf generator for the bylaws messes up the
greek characters for our motto a bit. Our motto is Ἀρετή Μονάζει."

Root cause: `generate_cnb_document_pdf_buffer` (`src/view/officer/cnb.py`)
renders every governing document with ReportLab's base-14 Times-Roman
font, which — like every ReportLab built-in font — only covers WinAnsi/
Latin-1. Greek (the motto, wherever it's quoted in a document's preamble
or body) has no glyph in that font at all, so it rendered as
missing-glyph boxes.

Fix: `esc()` (the one function every piece of document text passes
through before becoming Paragraph markup) now also wraps any run of
non-Latin-1 characters in a `<font name="DejaVuSerif">` tag —
`src/utils/pdf_fonts.py` registers a bundled, Greek-covering TTF once per
process. Ordinary Latin-1 text is untouched, so the rest of the document
keeps its existing Times-Roman appearance.

These tests generate a REAL PDF via the real function (not a mock) and
inspect it with PyMuPDF (already a project dependency —
`convert_pdf_to_images` in `src/view/view_document.py` uses it) to
confirm both what font actually got used and that the extracted text is
still correct — not just that no exception was raised.
"""
from django.test import TestCase

from src.models import GoverningDocument

MOTTO = 'Ἀρετή Μονάζει'


class CnbPdfRendersGreekMottoTests(TestCase):
    def setUp(self):
        self.doc = GoverningDocument.objects.create(
            doc_type='constitution',
            title='Constitution of Alpha Mu Chapter of Beta Theta Pi',
            preamble=f'Our motto is {MOTTO} (Virtue Stands Alone).',
            display_order=10,
        )

    def _generate_pdf_spans(self):
        import fitz

        from src.view.officer.cnb import generate_cnb_document_pdf_buffer

        buf = generate_cnb_document_pdf_buffer()
        pdf = fitz.open(stream=buf.getvalue(), filetype='pdf')
        spans = []
        for page in pdf:
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    spans.extend(line['spans'])
        pdf.close()
        return spans

    def test_extracted_text_still_contains_the_real_motto(self):
        """The most basic guarantee: whatever font is used, the actual
        Unicode text must survive the round trip through the PDF."""
        spans = self._generate_pdf_spans()
        full_text = ''.join(s['text'] for s in spans)
        self.assertIn(MOTTO, full_text)

    def test_greek_text_is_rendered_with_the_unicode_font(self):
        """The actual fix: Greek characters must use a font that has
        glyphs for them, not Times-Roman (which has none)."""
        spans = self._generate_pdf_spans()
        greek_spans = [s for s in spans if 'Ἀρετή' in s['text'] or 'Μονάζει' in s['text']]
        self.assertTrue(greek_spans, 'no span containing the Greek motto was found in the PDF at all')
        for span in greek_spans:
            self.assertEqual(
                span['font'], 'DejaVuSerif',
                f"Greek span {span['text']!r} used font {span['font']!r}, not the Unicode font",
            )

    def test_surrounding_english_text_still_uses_times_roman(self):
        """The fix must be scoped to the characters that actually need
        it — the rest of the document's appearance must be unchanged."""
        spans = self._generate_pdf_spans()
        english_spans = [s for s in spans if 'Our motto is' in s['text']]
        self.assertTrue(english_spans)
        for span in english_spans:
            self.assertEqual(span['font'], 'Times-Roman')

    def test_wrap_unicode_runs_leaves_ascii_alone(self):
        from src.utils.pdf_fonts import wrap_unicode_runs

        self.assertEqual(wrap_unicode_runs('Hello &amp; world'), 'Hello &amp; world')

    def test_wrap_unicode_runs_wraps_greek(self):
        from src.utils.pdf_fonts import wrap_unicode_runs

        result = wrap_unicode_runs(MOTTO)
        self.assertIn('<font name="DejaVuSerif">Ἀρετή</font>', result)
        self.assertIn('<font name="DejaVuSerif">Μονάζει</font>', result)
