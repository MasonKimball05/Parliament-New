"""
v3.45.0 (10-08-26) — rename a section from the amendment popup, and the
reference panel on the edit page.

Mason: "there's no way to edit the actual name of a section in the tracked
amendments section. Can you fix this oversight?"

Renaming already existed in the separate "New articles / sections / renames"
maker (v3.43.0). The amendment popup now carries a "Section title" box, and
`add_amendment` stores a changed title the same way the maker does: a
`rename_section` ResolutionStructureChange, applied when the resolution passes.

Run with: python manage.py test src.tests.legislation.test_cnb_amendment_title
"""
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment,
    ResolutionCollaborator, ResolutionStructureChange, Section,
)

TEXT = 'Dues shall be paid within thirty days.'


class _Base(TestCase):
    def setUp(self):
        self.chair = ParliamentUser.objects.create(
            user_id='CNB-T1', name='Chair', username='cnbt1', member_type='Member',
            member_status='Active', is_admin=True, email='cnbt1@example.com')
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        document = GoverningDocument.objects.filter(doc_type='bylaws').first() \
            or GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws')
        self.article = Article.objects.create(document=document, number='XCIX', title='Test', display_order=999)
        self.section = Section.objects.create(article=self.article, number='1', title='Dues',
                                              content=TEXT, display_order=1)
        self.client.force_login(self.chair)

    def post(self, **data):
        data.setdefault('section_id', self.section.pk)
        data.setdefault('proposed_text', TEXT)
        return self.client.post(reverse('cnb_add_amendment', args=[self.resolution.pk]), data)

    def renames(self):
        return ResolutionStructureChange.objects.filter(resolution=self.resolution, kind='rename_section')

    def amendments(self):
        return ResolutionAmendment.objects.filter(resolution=self.resolution)


class SectionTitleTests(_Base):
    def test_a_new_title_with_new_text_makes_an_amendment_and_a_rename(self):
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='Dues and Fees')
        self.assertEqual(self.amendments().count(), 1)
        rename = self.renames().get()
        self.assertEqual((rename.section, rename.title, rename.old_title, rename.applied),
                         (self.section, 'Dues and Fees', 'Dues', False))
        self.assertEqual(rename.added_by, self.chair)

    def test_only_the_title_changed_makes_a_rename_and_no_text_amendment(self):
        response = self.post(section_title='Dues and Fees')
        self.assertEqual(self.amendments().count(), 0)
        self.assertEqual(self.renames().get().title, 'Dues and Fees')
        said = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('its title becomes "Dues and Fees" when this passes' in m for m in said), said)

    def test_the_live_section_is_untouched_until_the_resolution_passes(self):
        self.post(section_title='Dues and Fees')
        self.section.refresh_from_db()
        self.assertEqual(self.section.title, 'Dues')

    def test_passing_the_resolution_applies_the_title(self):
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='Dues and Fees')
        status = reverse('cnb_set_status', args=[self.resolution.pk])
        self.client.post(status, {'status': 'pending'})
        self.client.post(status, {'status': 'passed'})
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.status, 'passed')
        self.section.refresh_from_db()
        self.assertEqual(self.section.title, 'Dues and Fees')
        self.assertIn('fourteen', self.section.content)

    def test_same_title_changes_nothing(self):
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='Dues')
        self.assertFalse(self.renames().exists())
        self.assertEqual(self.amendments().count(), 1)

    def test_saving_again_updates_the_one_rename(self):
        self.post(section_title='Dues and Fees')
        self.post(section_title='Dues, Fees and Fines')
        self.assertEqual([r.title for r in self.renames()], ['Dues, Fees and Fines'])

    def test_putting_the_current_title_back_withdraws_the_rename(self):
        self.post(section_title='Dues and Fees')
        self.post(section_title='Dues')
        self.assertFalse(self.renames().exists())
        self.assertEqual(self.amendments().count(), 0)

    def test_blank_or_missing_title_is_ignored(self):
        self.post(section_title='Dues and Fees')
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='   ')
        self.post(proposed_text=TEXT.replace('thirty', 'ten'))            # an old tab: no box at all
        self.assertEqual(self.renames().get().title, 'Dues and Fees')

    def test_whitespace_is_tidied_and_length_capped(self):
        self.post(section_title='  Dues   and\tFees  ' + 'x' * 300)
        title = self.renames().get().title
        self.assertTrue(title.startswith('Dues and Fees x'))
        self.assertEqual(len(title), 200)

    def test_striking_the_whole_section_does_not_rename_it(self):
        self.post(proposed_text='', section_title='Dues and Fees')
        self.assertFalse(self.renames().exists())
        self.assertEqual(self.amendments().get().amendment_type, 'deletion')

    def test_a_rename_made_in_the_maker_is_the_same_record(self):
        self.client.post(reverse('cnb_add_structure_change', args=[self.resolution.pk]),
                         {'kind': 'rename_section', 'section_id': self.section.pk, 'title': 'From the maker'})
        self.post(section_title='From the popup')
        self.assertEqual([r.title for r in self.renames()], ['From the popup'])

    def test_a_plain_member_cannot_rename(self):
        member = ParliamentUser.objects.create(
            user_id='CNB-T2', name='Member', username='cnbt2', member_type='Member',
            member_status='Active', email='cnbt2@example.com')
        self.client.force_login(member)
        self.post(section_title='Hijacked')
        self.assertFalse(self.renames().exists())

    def test_an_editor_collaborator_can(self):
        editor = ParliamentUser.objects.create(
            user_id='CNB-T3', name='Editor', username='cnbt3', member_type='Member',
            member_status='Active', email='cnbt3@example.com')
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=editor, role='editor')
        self.client.force_login(editor)
        self.post(section_title='Dues and Fees')
        self.assertEqual(self.renames().get().added_by, editor)

    def test_a_closed_resolution_cannot_be_renamed_into(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='passed')
        self.post(section_title='Dues and Fees')
        self.assertFalse(self.renames().exists())


class PagesTests(_Base):
    PAGES = ('cnb_resolution_detail', 'cnb_edit_resolution')

    def page(self, name):
        response = self.client.get(reverse(name, args=[self.resolution.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_popup_has_the_title_box_and_options_carry_titles(self):
        for name in self.PAGES:
            with self.subTest(page=name):
                html = self.page(name)
                self.assertEqual(html.count('name="section_title" id="sectionTitleInput"'), 1)
                self.assertIn(f'<option value="{self.section.pk}" data-title="Dues"', html)
                self.assertIn('id="pending-section-titles"', html)

    def test_row_shows_the_proposed_title(self):
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='Dues & Fees')
        for name in self.PAGES:
            with self.subTest(page=name):
                html = self.page(name)
                self.assertIn('New title when this passes:</span> Dues &amp; Fees', html)
                self.assertIn(f'"{self.section.pk}": "Dues \\u0026 Fees"', html)

    def test_proposed_title_costs_one_query_however_many_amendments(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries():
            url = reverse('cnb_edit_resolution', args=[self.resolution.pk])
            self.client.get(url)
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(url)
            return len(ctx)

        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'), section_title='Dues and Fees')
        one = queries()
        for n in range(2, 7):
            section = Section.objects.create(article=self.article, number=str(n), title=f'S{n}',
                                             content=TEXT, display_order=n)
            self.post(section_id=section.pk, proposed_text=TEXT.replace('thirty', str(n)), section_title=f'New {n}')
        self.assertEqual(queries(), one)

    def test_edit_page_has_the_reference_panel_and_the_other_pages_do_not(self):
        self.post(proposed_text=TEXT.replace('thirty', 'fourteen'))
        edit = self.page('cnb_edit_resolution')
        self.assertEqual(edit.count('id="refInserter"'), 1)
        self.assertIn('class="js-ref-status text-xs"', edit)
        self.assertIn('data-section-title="Dues"', edit)
        self.assertIn('if (window.refreshRefInserter) window.refreshRefInserter();', edit)
        self.assertNotIn('id="refInserter"', self.page('cnb_resolution_detail'))
        self.assertNotIn('id="refInserter"', self.client.get(reverse('cnb_create_resolution')).content.decode())

    def test_reference_panel_builds_text_not_html(self):
        from pathlib import Path
        from django.conf import settings
        for name in ('_ref_inserter.html', '_amendment_title.html'):
            script = (Path(settings.BASE_DIR) / 'templates' / 'cnb' / name).read_text(encoding='utf-8')
            self.assertNotIn('innerHTML', script, name)
