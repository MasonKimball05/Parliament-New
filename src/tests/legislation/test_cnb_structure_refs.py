"""
v3.50.2 — "Copy [ref]" for new articles, sections and renames.

Mason, 10-18-26: the resolution editor's "New Articles, Sections & Renames"
list had no way to copy ref codes. Tracked amendments did (v3.45.0): copy,
insert from the corner panel, and the print preview states the motion where
the ref is.

A structural change's ref is "[Change S<pk>: <heading>]" (src/cnb_structure.ref_code).
It is found by "[Change S<pk>:" alone, because the heading after the colon
moves (renumbering, title edits). The JS that finds it
(`cnbStructureRefPattern`, the ref panel, the print preview, the citation
chips) runs in the browser and was checked in Chromium on 10-18-26 (there are
no browser tests in the suite yet).

Run with: python manage.py test src.tests.legislation.test_cnb_structure_refs
"""
import re

from django.urls import reverse

from src import cnb_structure
from src.models import ResolutionStructureChange
from src.tests.legislation.test_cnb_structure_changes import _Base


class RefCodeTests(_Base):
    def make_section(self, title='Dues'):
        self.add(kind='new_section', article_id=self.arts[2].pk, before_section_id=self.secs[1].pk,
                 title=title, content='New text.')
        return ResolutionStructureChange.objects.latest('pk')

    def test_code_starts_with_the_stable_id_then_the_heading(self):
        change = self.make_section()
        code = cnb_structure.ref_code(change)
        self.assertTrue(code.startswith(f'Change S{change.pk}: New section: '), code)
        self.assertIn('§ 2 — Dues', code)

    def test_square_brackets_in_a_title_cannot_end_the_ref_early(self):
        change = self.make_section(title='Dues [draft]')
        code = cnb_structure.ref_code(change)
        self.assertNotIn('[', code)
        self.assertNotIn(']', code)
        self.assertIn('Dues draft', code)

    def test_edit_page_has_a_copy_button_per_change(self):
        change = self.make_section()
        html = self.client.get(reverse('cnb_edit_resolution', args=[self.resolution.pk])).content.decode()
        self.assertIn('js-copy-structure-ref', html)
        self.assertIn(f'data-change-id="{change.pk}"', html)
        self.assertIn(f'data-ref="Change S{change.pk}: New section:', html)
        self.assertIn('window.cnbStructureRefPattern', html)

    def test_read_page_has_no_copy_button(self):
        self.make_section()
        html = self.client.get(reverse('cnb_resolution_detail', args=[self.resolution.pk])).content.decode()
        self.assertNotIn('js-copy-structure-ref', html)

    def test_print_page_carries_the_motion_escaped(self):
        self.add(kind='new_section', article_id=self.arts[2].pk, before_section_id='',
                 title='<b>Bold</b>', content='<script>alert(1)</script>')
        change = ResolutionStructureChange.objects.latest('pk')
        html = self.client.get(reverse('cnb_resolution_print', args=[self.resolution.pk])).content.decode()
        motion = re.search(r'<template class="js-structure-motion" data-change-id="%d">(.*?)</template>' % change.pk,
                           html, re.S)
        self.assertIsNotNone(motion)
        self.assertIn('Upon passage: New section:', motion.group(1))
        self.assertIn('&lt;b&gt;Bold&lt;/b&gt;', motion.group(1))
        self.assertIn('&lt;script&gt;', motion.group(1))
        self.assertNotIn('<script>alert(1)', html)
