"""
v3.29.9 — Mason: "On the /constitution-bylaws/ page can you add a view
document PDF button that generates a current up to date document with all
the articles, sections, appendix, cover page + foreword (once this gets
passed and the flag is enabled)?"

New `generate_cnb_document_pdf_buffer()` / `cnb_document_pdf` view
(src/view/officer/cnb.py) builds a single PDF live from
`GoverningDocument.enabled()` — the same queryset the Document tab itself
renders from — so it automatically includes exactly the documents a member
can currently see on-screen, in the same order, respecting each document's
own feature flag. That means the Foreword inclusion rule falls out of
`GoverningDocument.enabled()`'s existing fail-closed behaviour for free:
no separate logic was written for "once this gets passed and the flag is
enabled" because that's already what the flag check does.

Deliberately distinct from the pre-existing "Official PDF" link (a static
uploaded file, not automatically kept in sync) — the new PDF's own cover
page says so, and the two links now sit side by side in the sidebar.
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

import fitz  # PyMuPDF

from src.models import Article, GoverningDocument, ParliamentUser, Section
from src.models_feature_flags import FeatureFlag
from src.view.officer.cnb import generate_cnb_document_pdf_buffer


def make_user(uid='cnb-pdf-user', **kwargs):
    defaults = dict(name='C&B PDF User', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('cnb-pdf-test-pass-12345!')
    user.save()
    return user


def make_document(doc_type, *, with_article=True, **kwargs):
    doc = GoverningDocument.objects.create(
        doc_type=doc_type, title=kwargs.pop('title', doc_type.title()), **kwargs,
    )
    if with_article:
        article = Article.objects.create(document=doc, number='I', title='An Article')
        Section.objects.create(article=article, number='1', content='Section text here.')
    return doc


def _pdf_text(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    text = '\n'.join(page.get_text() for page in doc)
    doc.close()
    return text


def _lines_with_x(pdf_bytes):
    """Return [(x0, text), ...] for every text line in the PDF, in reading
    order — used to check actual left-indentation, not just presence of
    text, since a "hard to read, no indentation" bug produces the right
    words at the wrong horizontal position."""
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    out = []
    for page in doc:
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                text = ''.join(s['text'] for s in line['spans'])
                if text.strip():
                    out.append((line['bbox'][0], text))
    doc.close()
    return out


def _fonts_used(pdf_bytes):
    """See test_minutes_pdf_font.py's identical helper for why this reads
    `get_text('dict')` rather than `page.get_fonts()` — ReportLab's Canvas
    always declares a phantom, unused 'Helvetica' resource regardless of
    what's actually painted."""
    doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    names = set()
    for page in doc:
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    if span['text'].strip():
                        names.add(span['font'])
    doc.close()
    return names


class CnbDocumentPdfContentTests(TestCase):
    def setUp(self):
        cache.clear()
        # display_order matters here: GoverningDocument.Meta.ordering is
        # ['display_order', 'doc_type'], and the default display_order=0
        # for both would tiebreak alphabetically on doc_type ('bylaws' <
        # 'constitution') — set explicitly, matching the real seed data's
        # convention (Foreword 0, Constitution 10, Bylaws 20, Appendix 30).
        self.constitution = make_document('constitution', title='Constitution of Alpha Mu', display_order=10)
        self.bylaws = make_document('bylaws', title='Bylaws of Alpha Mu', display_order=20)

    def test_pdf_includes_enabled_documents_in_order(self):
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('Constitution of Alpha Mu', text)
        self.assertIn('Bylaws of Alpha Mu', text)
        self.assertLess(text.index('Constitution of Alpha Mu'), text.index('Bylaws of Alpha Mu'))

    def test_pdf_includes_article_and_section_content(self):
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('An Article', text)
        self.assertIn('Section text here.', text)

    def test_cover_page_distinguishes_from_official_pdf(self):
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('Constitution & Bylaws', text)
        self.assertIn('generated directly from', text.lower())
        self.assertIn('Official PDF', text)

    def test_uses_times_not_helvetica(self):
        buf = generate_cnb_document_pdf_buffer()
        fonts = _fonts_used(buf.getvalue())
        offenders = {f for f in fonts if 'helvetica' in f.lower()}
        self.assertEqual(offenders, set(), f"PDF still uses: {offenders} (all: {fonts})")
        times_fonts = {f for f in fonts if 'times' in f.lower()}
        self.assertTrue(times_fonts, f"Expected Times fonts, found: {fonts}")

    def test_suspended_section_is_marked(self):
        article = self.constitution.articles.first()
        section = article.sections.first()
        section.is_active = False
        section.deactivation_reason = 'IFC ruling 2026-01-01'
        section.save()
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('SUSPENDED', text)
        self.assertIn('IFC ruling 2026-01-01', text)

    def test_suspended_article_is_marked(self):
        article = self.constitution.articles.first()
        article.is_active = False
        article.deactivation_reason = 'Suspended pending review'
        article.save()
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('SUSPENDED', text)
        self.assertIn('Suspended pending review', text)

    def test_amendment_protected_section_shows_protection_note(self):
        import datetime
        section = self.constitution.articles.first().sections.first()
        section.amendment_protected = True
        section.protected_until = datetime.date(2027, 1, 1)
        section.save()
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('Protected from new amendments', text)

    def test_partial_suspension_is_listed(self):
        section = self.constitution.articles.first().sections.first()
        section.partial_suspensions = [
            {'ref': '1.a', 'reason': 'Pending Kai review', 'suspended_at': '2026-01-01'},
        ]
        section.save()
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('1.a', text)
        self.assertIn('partially suspended', text)
        self.assertIn('Pending Kai review', text)

    def test_preamble_text_is_included(self):
        self.constitution.preamble = 'We the members of Alpha Mu do hereby establish this Constitution.'
        self.constitution.save()
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('We the members of Alpha Mu', text)

    def test_sections_are_indented_under_their_article_with_paragraph_tabs(self):
        """
        v3.29.10 — Mason: "there are no tab spaces in the generated pdf at
        all so it's hard to read." Everything (Article headings, § body
        text, notes) was flush against the same left margin with no visual
        hierarchy. Fixed with real ReportLab `leftIndent`/`firstLineIndent`
        on the ParagraphStyles, verified here by actual x-position rather
        than just checking the words are present — the words were already
        present before this fix; the position is what was wrong.
        """
        article = self.constitution.articles.first()
        section = article.sections.first()
        section.content = (
            'This is a long section of text that should wrap across more '
            'than one line so the first-line indent can be checked against '
            'the position of a wrapped continuation line within the same '
            'paragraph, which must fall back to the section-level indent '
            'rather than repeating the paragraph tab.'
        )
        section.save()

        buf = generate_cnb_document_pdf_buffer()
        lines = _lines_with_x(buf.getvalue())

        article_x = next(x for x, t in lines if 'ARTICLE' in t)
        section_heading_x = next(x for x, t in lines if t.strip().startswith('§'))
        body_lines = [(x, t) for x, t in lines if 'long section of text' in t or 'wrapped continuation' in t or 'section-level indent' in t]
        self.assertGreaterEqual(len(body_lines), 2, f"Expected the section body to wrap across multiple lines: {lines}")
        first_line_x = body_lines[0][0]
        wrapped_line_x = body_lines[1][0]

        # Section heading is indented under its Article.
        self.assertGreater(section_heading_x, article_x)
        # The first line of the paragraph gets an extra "tab" beyond the
        # section's own indent...
        self.assertGreater(first_line_x, section_heading_x)
        # ...but a wrapped continuation line of the SAME paragraph falls
        # back to the section's indent level, not the article's margin and
        # not another first-line tab.
        self.assertAlmostEqual(wrapped_line_x, section_heading_x, delta=1.0)
        self.assertGreater(wrapped_line_x, article_x)

    def test_every_numbered_clause_is_indented_not_just_the_first(self):
        """
        v3.29.11 — Mason, after v3.29.10 shipped the tab fix above: "Now
        only the first thing under each article is indented, not
        everything." Reproduced only once real C&B content was used
        instead of a single-prose-paragraph fixture: real section content
        stores one numbered/lettered clause per line
        ("1. Eligible members...\n2. Members must not...") rather than as
        blank-line-separated paragraphs — 44 of 51 real sections with any
        line break use single-newline separation, not blank lines. The old
        code ran that whole field through one `Paragraph` with `<br/>`
        between lines, and ReportLab's `firstLineIndent` only tabs a
        Paragraph's true first line — so every clause after the first one
        in a section ran flush left, exactly matching Mason's screenshot.

        This fixture mirrors the real shape: a flat numbered list (no
        blank lines) plus one nested lettered sub-item marked by a 3-space
        leading indent, the same convention the real Bylaws officer-duties
        list uses (confirmed against seeded production data).
        """
        section = self.constitution.articles.first().sections.first()
        section.content = (
            '1. First clause of this section.\n'
            '2. Second clause of this section.\n'
            '3. Third clause of this section.\n'
            '   a. A nested sub-item under the third clause.'
        )
        section.save()

        buf = generate_cnb_document_pdf_buffer()
        lines = _lines_with_x(buf.getvalue())

        def x_for(needle):
            return next(x for x, t in lines if needle in t)

        first_x = x_for('First clause')
        second_x = x_for('Second clause')
        third_x = x_for('Third clause')
        nested_x = x_for('nested sub-item')

        # Every top-level clause gets the same first-line tab — not just
        # clause 1. This is the assertion that fails against the pre-fix
        # code (clauses 2 and 3 land back at the section's plain indent,
        # with no tab).
        self.assertAlmostEqual(first_x, second_x, delta=0.5)
        self.assertAlmostEqual(second_x, third_x, delta=0.5)

        # The lettered sub-item nests one level deeper than its parent
        # numbered clause, mirroring the plain-text leading-space
        # convention (and the on-screen `white-space: pre-wrap` viewer).
        self.assertGreater(nested_x, third_x)

    def test_blank_line_between_clauses_adds_space_not_a_dropped_line(self):
        """A blank line in section content is still meaningful (it
        separates one top-level numbered item from the next in the real
        Bylaws content) — confirm it survives as extra spacing rather than
        silently vanishing once every line became its own Paragraph."""
        section = self.constitution.articles.first().sections.first()
        section.content = (
            '1. President\n'
            '   a. Be the face of the fraternity.\n'
            '\n'
            '2. Executive Vice President\n'
            '   a. Troubleshoot and monitor.'
        )
        section.save()

        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('1. President', text)
        self.assertIn('Be the face of the fraternity.', text)
        self.assertIn('2. Executive Vice President', text)
        self.assertIn('Troubleshoot and monitor.', text)


class CnbDocumentPdfForewordFlagTests(TestCase):
    """
    The central behaviour Mason asked for: the Foreword appears in the PDF
    only once it has passed and its flag is on — and that's not new logic,
    it's `GoverningDocument.enabled()`'s existing fail-closed rule (see
    test_cnb_foreword.py), which this generator deliberately reuses rather
    than re-querying `GoverningDocument.objects.all()`.
    """

    def setUp(self):
        cache.clear()
        self.foreword = make_document(
            'foreword', with_article=False,
            title='Foreword', preamble='A message to future Betas.',
        )
        self.constitution = make_document('constitution', title='Constitution')

    def test_foreword_excluded_with_no_flag_rows_at_all(self):
        self.assertEqual(FeatureFlag.objects.count(), 0)
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertNotIn('A message to future Betas.', text)
        self.assertIn('Constitution', text)

    def test_foreword_excluded_when_flag_explicitly_off(self):
        FeatureFlag.objects.create(name='cnb_foreword', is_enabled=False)
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertNotIn('A message to future Betas.', text)

    def test_foreword_included_once_flag_is_on(self):
        FeatureFlag.objects.create(name='cnb_foreword', is_enabled=True)
        buf = generate_cnb_document_pdf_buffer()
        text = _pdf_text(buf.getvalue())
        self.assertIn('A message to future Betas.', text)


class CnbDocumentPdfViewTests(TestCase):
    def setUp(self):
        cache.clear()
        make_document('constitution')
        self.user = make_user()

    def test_requires_login(self):
        response = self.client.get(reverse('cnb_document_pdf'))
        self.assertNotEqual(response.status_code, 200)

    def test_any_logged_in_member_can_view_it(self):
        self.client.login(username=self.user.username, password='cnb-pdf-test-pass-12345!')
        response = self.client.get(reverse('cnb_document_pdf'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('inline', response['Content-Disposition'])

    def test_button_is_on_the_document_tab(self):
        self.client.login(username=self.user.username, password='cnb-pdf-test-pass-12345!')
        response = self.client.get(reverse('constitution_bylaws'))
        self.assertContains(response, reverse('cnb_document_pdf'))
        self.assertContains(response, 'View Document PDF')

    def test_button_also_appears_in_the_banner_next_to_new_resolution(self):
        """
        Two entry points: the sidebar link (Document tab) and a banner
        button next to "New Resolution", visible regardless of which tab
        is active. The link to cnb_document_pdf should appear twice on the
        page — once per button — and the banner one must NOT be gated
        behind CNB permission the way "New Resolution" is, since viewing
        the document is member-facing.
        """
        self.client.login(username=self.user.username, password='cnb-pdf-test-pass-12345!')
        response = self.client.get(reverse('constitution_bylaws'))
        pdf_url = reverse('cnb_document_pdf')
        self.assertEqual(response.content.decode().count(pdf_url), 2)
        # This user has no CNB permission, so "New Resolution" must be
        # absent while "View Document PDF" is still present.
        self.assertNotContains(response, 'New Resolution')
        self.assertContains(response, 'View Document PDF')
