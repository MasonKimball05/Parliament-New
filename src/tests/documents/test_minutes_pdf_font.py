"""
v3.29.8 — Mason: "make sure the other pdf makers like the minutes ones
also use Times New Roman", after the CNB resolution previewer/PDF turned
out to be defaulting to a non-Times serif.

`generate_minutes_pdf_buffer()` (src/view/officer/chapter_minutes.py,
shared by both chapter and committee minutes export/download/publish)
builds every ReportLab `ParagraphStyle` from `getSampleStyleSheet()`'s
defaults, which are Helvetica-based — none of the styles actually used as
a `parent=` explicitly named a font, so the entire PDF (title, body,
section/motion headers, attendance names, markdown headings) rendered in
Helvetica regardless of the CNB documents elsewhere in the app being
styled as Times. Fixed by overriding the base styles the ParagraphStyles
inherit from (`styles['Normal']`, `['Title']`, `['Heading2']`,
`['Heading3']`) to ReportLab's built-in Times family, plus the three
markdown heading styles that named 'Helvetica-Bold'/'Helvetica-BoldOblique'
explicitly rather than inheriting.

This doesn't just read the source for the fix — it actually generates a
PDF from a real ChapterMinutes fixture exercising every code path (title,
attendance table, a text section with markdown headings, a motion, an
edit-after-publish notice) and opens the real output bytes with PyMuPDF
to read which fonts the PDF actually embeds, which is the only way to
catch a ReportLab font bug: the Python objects can look right and the
actual PDF can still come out in the wrong font if any style resolves its
`fontName` from an un-overridden parent.
"""
from datetime import date, time

import fitz  # PyMuPDF
from django.test import TestCase

from src.models import (
    ChapterMinutes, Committee, MinutesSection, MinutesMotion, ParliamentUser,
)
from src.view.officer.chapter_minutes import generate_minutes_pdf_buffer


def _officer(user_id='MPF-1', name='Minutes Author'):
    return ParliamentUser.objects.create_user(
        user_id=user_id, password='minutes-pdf-font-test-12345!',
        name=name, username=user_id.lower().replace('-', '_'),
        member_type='Officer',
    )


def _fonts_used(pdf_bytes):
    """
    Return the set of font names actually painted onto visible glyphs.

    Deliberately NOT `page.get_fonts()` — ReportLab's `Canvas` always
    declares a 'Helvetica' resource (F1) as part of its default graphics
    state, whether or not anything is ever drawn with it, so
    `get_fonts()` reports a phantom 'Helvetica' entry on every ReportLab
    PDF regardless of what any style actually uses (confirmed directly:
    a minimal ReportLab doc built entirely from a Times-Roman style still
    lists a 'Helvetica' font resource). Reading `get_text("dict")`'s
    per-span `font` field instead reports what glyphs were actually set
    with, which is what "does this PDF look like Times New Roman"
    actually means.
    """
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


def _build_minutes(creator, committee=None, with_edit_notice=False):
    minutes = ChapterMinutes.objects.create(
        title='Fall Chapter Meeting', date=date(2026, 9, 3),
        start_time=time(19, 0), end_time=time(20, 15),
        committee=committee, created_by=creator, status='finalized',
        attendance_data=[
            {'name': 'Alice Member', 'status': 'present'},
            {'name': 'Bob Member', 'status': 'present'},
            {'name': 'Cara Member', 'status': 'absent'},
            {'name': 'Dan Member', 'status': 'excused'},
        ],
    )
    if with_edit_notice:
        minutes.edited_after_publish = True
        from django.utils import timezone
        minutes.last_edit_at = timezone.now()
        minutes.last_edit_by = creator
        minutes.last_edit_reason = 'Corrected a typo in the motion text.'
        minutes.save()

    MinutesSection.objects.create(
        minutes=minutes, section_type='text', order=1,
        content='# Officer Reports\n\nThe president gave an update on **recruitment**.\n\n'
                '## Treasury\n\nDues are on track.\n\n'
                '### Sub-point\n\nNo further action needed.',
    )
    motion_section = MinutesSection.objects.create(
        minutes=minutes, section_type='motion', order=2,
    )
    MinutesMotion.objects.create(
        section=motion_section, motion_type='custom',
        motion_text='Motion to approve the fall budget.',
        author=creator, received_second=True, seconded_by_text='Bob Member',
        vote_method='voice', result='passed',
        votes_for=20, votes_against=1, votes_abstain=0,
    )
    return minutes


class MinutesPdfUsesTimesNotHelveticaTests(TestCase):
    """Renders a real PDF and reads back which fonts it actually contains."""

    def test_chapter_minutes_pdf_has_no_helvetica(self):
        creator = _officer()
        minutes = _build_minutes(creator, with_edit_notice=True)
        buf = generate_minutes_pdf_buffer(minutes)
        fonts = _fonts_used(buf.getvalue())
        offenders = {f for f in fonts if 'helvetica' in f.lower() or f.lower() in ('arial',)}
        self.assertEqual(
            offenders, set(),
            f"PDF still uses non-Times fonts: {offenders} (all fonts: {fonts})",
        )

    def test_chapter_minutes_pdf_actually_uses_times(self):
        creator = _officer('MPF-2')
        minutes = _build_minutes(creator)
        buf = generate_minutes_pdf_buffer(minutes)
        fonts = _fonts_used(buf.getvalue())
        times_fonts = {f for f in fonts if 'times' in f.lower()}
        self.assertTrue(
            times_fonts,
            f"Expected at least one Times font in the PDF, found: {fonts}",
        )

    def test_committee_minutes_pdf_also_has_no_helvetica(self):
        """Committee minutes go through the exact same generator — confirm
        the fix isn't accidentally scoped to the chapter-only code path."""
        creator = _officer('MPF-3')
        committee = Committee.objects.create(name='Test Committee', code='TESTC')
        minutes = _build_minutes(creator, committee=committee)
        buf = generate_minutes_pdf_buffer(minutes)
        fonts = _fonts_used(buf.getvalue())
        offenders = {f for f in fonts if 'helvetica' in f.lower()}
        self.assertEqual(offenders, set(), f"Committee minutes PDF still uses: {offenders}")

    def test_code_block_stays_monospace_not_times(self):
        """Deliberate exception: inline/fenced code should stay Courier,
        not be swept into the Times change along with everything else."""
        creator = _officer('MPF-4')
        minutes = _build_minutes(creator)
        MinutesSection.objects.create(
            minutes=minutes, section_type='text', order=3,
            content='```\ncode block content\n```',
        )
        buf = generate_minutes_pdf_buffer(minutes)
        fonts = _fonts_used(buf.getvalue())
        courier_fonts = {f for f in fonts if 'courier' in f.lower()}
        self.assertTrue(courier_fonts, f"Expected a Courier font for the code block, found: {fonts}")
