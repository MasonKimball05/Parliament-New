"""
v3.45.0 (10-08-26) — filter the "Tracked Section Amendments" list.

Mason: "In tracked amendments can you add a way to filter? Like if I only want
the constitution amendments or only bylaws or only certain types of
amendments."

The filter is a script (templates/cnb/_amendment_filter.html) that reads each
row's document and type from data attributes. The suite has no browser, so
this pins what the script depends on: both pages mark their rows, carry the
bar and the "nothing matches" line, and load the script once.

Run with: python manage.py test src.tests.legislation.test_cnb_amendment_filter
"""
import re

from django.test import TestCase
from django.urls import reverse

from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment, Section,
)

PAGES = ('cnb_resolution_detail', 'cnb_edit_resolution')


class AmendmentFilterTests(TestCase):
    def setUp(self):
        self.chair = ParliamentUser.objects.create(
            user_id='CNB-F1', name='Chair', username='cnbf1', member_type='Member',
            member_status='Active', is_admin=True, email='cnbf1@example.com')
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        self.client.force_login(self.chair)

    def amend(self, doc_type, kind, proposed):
        document = GoverningDocument.objects.filter(doc_type=doc_type).first() \
            or GoverningDocument.objects.create(doc_type=doc_type, title=doc_type.title())
        article = Article.objects.create(document=document, number=f'F{Article.objects.count()}',
                                         title='A', display_order=900 + Article.objects.count())
        section = Section.objects.create(article=article, number='1', title='S', content='Old text.', display_order=1)
        return ResolutionAmendment.objects.create(
            resolution=self.resolution, section=section, proposed_text=proposed,
            original_text_snapshot='Old text.', amendment_type=kind)

    def page(self, name):
        response = self.client.get(reverse(name, args=[self.resolution.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def rows(self, html):
        return re.findall(r'class="js-amendment-row[^"]*" data-doc="([^"]*)" data-doc-label="([^"]*)" data-type="([^"]*)"', html)

    def test_each_row_carries_its_document_and_type(self):
        self.amend('constitution', 'change', 'New text.')
        self.amend('bylaws', 'addition', 'Old text. More.')
        self.amend('bylaws', 'deletion', '')
        for name in PAGES:
            with self.subTest(page=name):
                self.assertCountEqual(self.rows(self.page(name)), [
                    ('constitution', 'Constitution', 'change'),
                    ('bylaws', 'Bylaws', 'addition'),
                    ('bylaws', 'Bylaws', 'deletion'),
                ])

    def test_pages_carry_the_bar_the_empty_line_and_one_script(self):
        self.amend('constitution', 'change', 'New text.')
        for name in PAGES:
            with self.subTest(page=name):
                html = self.page(name)
                self.assertEqual(html.count('class="js-amendment-filter hidden'), 1)
                self.assertEqual(html.count('class="js-amendment-none hidden'), 1)
                self.assertEqual(html.count('window.wireAmendmentFilter = wire;'), 1)

    def test_no_amendments_no_bar(self):
        for name in PAGES:
            with self.subTest(page=name):
                html = self.page(name)
                self.assertNotIn('class="js-amendment-filter', html)
                self.assertEqual(self.rows(html), [])

    def test_the_edit_page_rewires_the_filter_after_a_live_redraw(self):
        self.amend('constitution', 'change', 'New text.')
        self.assertIn('if (window.wireAmendmentFilter) window.wireAmendmentFilter(card);',
                      self.page('cnb_edit_resolution'))

    def test_chips_are_built_as_text(self):
        """Document labels come from the page; they go in with textContent."""
        from pathlib import Path
        from django.conf import settings
        script = (Path(settings.BASE_DIR) / 'templates' / 'cnb' / '_amendment_filter.html').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML', script)
