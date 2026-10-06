"""
New articles / sections with renumbering, and renames (v3.43.0, 10-02-26).

Mason: "add an article + section maker to the edit page ... if someone wants
to put in a new article 4 it moves the existing article 4 to 5, 5 to 6, etc."
and "a way to edit the name of articles + sections too."

Design: `src/cnb_structure.py`. What these tests guard:

1. a proposal changes NOTHING in the live document until the resolution
   passes;
2. passing inserts in the right place and renumbers everything after it,
   and only that;
3. a passed resolution keeps citing the numbers it was voted on with, even
   after a later renumbering;
4. what cannot be renumbered is refused when drafted, and a pass that cannot
   apply rolls back whole;
5. only the chair and the resolution's editors can propose.

Run with: python manage.py test src.tests.legislation.test_cnb_structure_changes
"""
from django.test import TestCase
from django.urls import reverse

from src import cnb_structure
from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment,
    ResolutionCollaborator, ResolutionStructureChange, Section, SectionRevision,
)


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


class _Base(TestCase):
    def setUp(self):
        self.chair = make_user('ST-H1', is_admin=True)
        self.editor = make_user('ST-E1')
        self.member = make_user('ST-M1')
        # Start from a known shape: any seeded articles in this document are cleared.
        self.doc = GoverningDocument.objects.filter(doc_type='bylaws').first() \
            or GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws')
        self.doc.articles.all().delete()
        self.arts = [
            Article.objects.create(document=self.doc, number=n, title=t, display_order=i)
            for i, (n, t) in enumerate([('I', 'Name'), ('II', 'Members'), ('III', 'Officers'), ('IV', 'Meetings')], 1)
        ]
        self.secs = [
            Section.objects.create(article=self.arts[2], number=str(i), title=f'Sec {i}',
                                   content=f'Text {i}. See Article IV, Section 1 of the Bylaws.', display_order=i)
            for i in (1, 2, 3)
        ]
        self.resolution = Resolution.objects.create(title='Restructure', created_by=self.chair)
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')
        self.client.force_login(self.chair)

    def numbers(self):
        return [(a.number, a.title) for a in self.doc.articles.order_by('display_order')]

    def section_numbers(self, article=None):
        article = article or self.arts[2]
        return [(s.number, s.title) for s in article.sections.order_by('display_order')]

    def add(self, **data):
        return self.client.post(reverse('cnb_add_structure_change', args=[self.resolution.pk]), data)

    def pass_it(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='pending')
        return self.client.post(reverse('cnb_set_status', args=[self.resolution.pk]), {'status': 'passed'})


class RomanTests(TestCase):
    def test_round_trip(self):
        for n in (1, 4, 9, 14, 19, 40, 49, 90, 399):
            self.assertEqual(cnb_structure.roman_to_int(cnb_structure.int_to_roman(n)), n)

    def test_rejects_what_is_not_a_numeral(self):
        for bad in ('', 'IIII', 'VX', '4', 'A', 'IC'):
            self.assertIsNone(cnb_structure.roman_to_int(bad), bad)


class NewArticleTests(_Base):

    def test_drafting_changes_nothing(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        self.assertEqual(ResolutionStructureChange.objects.count(), 1)
        self.assertEqual(self.numbers(), [('I', 'Name'), ('II', 'Members'), ('III', 'Officers'), ('IV', 'Meetings')])

    def test_description_says_what_will_move(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        says = cnb_structure.describe(ResolutionStructureChange.objects.get())
        self.assertEqual(says['heading'], 'New article: Bylaws Article III — Standards')
        self.assertEqual(says['renumbering'], 'Current Articles III–IV become IV–V.')

    def test_passing_inserts_and_renumbers_what_follows(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        self.pass_it()
        self.assertEqual(self.numbers(), [('I', 'Name'), ('II', 'Members'), ('III', 'Standards'),
                                          ('IV', 'Officers'), ('V', 'Meetings')])
        change = ResolutionStructureChange.objects.get()
        self.assertTrue(change.applied)
        self.assertEqual(change.result_label, 'Bylaws Article III')
        # The sections moved with their article.
        self.assertEqual(Section.objects.get(pk=self.secs[0].pk).full_identifier, 'Bylaws Art. IV § 1')

    def test_at_the_end_renumbers_nothing(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id='', title='Amendments')
        self.pass_it()
        self.assertEqual(self.numbers()[-1], ('V', 'Amendments'))
        self.assertEqual(self.numbers()[:4], [('I', 'Name'), ('II', 'Members'), ('III', 'Officers'), ('IV', 'Meetings')])

    def test_two_new_articles_in_one_resolution(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[0].pk, title='Preamble')
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[3].pk, title='Standards')
        self.pass_it()
        self.assertEqual([t for _n, t in self.numbers()],
                         ['Preamble', 'Name', 'Members', 'Officers', 'Standards', 'Meetings'])
        self.assertEqual([n for n, _t in self.numbers()], ['I', 'II', 'III', 'IV', 'V', 'VI'])
        labels = sorted(ResolutionStructureChange.objects.values_list('result_label', flat=True))
        self.assertEqual(labels, ['Bylaws Article I', 'Bylaws Article V'])

    def test_a_number_that_cannot_shift_is_refused_when_drafted(self):
        self.arts[3].number = 'IV-A'
        self.arts[3].save()
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        self.assertFalse(ResolutionStructureChange.objects.exists())

    def test_references_that_will_be_stranded_are_listed(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        refs = cnb_structure.references_to_check(ResolutionStructureChange.objects.get())
        self.assertEqual(len(refs), 3)                       # each section cites Article IV
        self.assertIn('Article IV, Section 1', refs[0]['text'])
        # ...and the text itself is not rewritten on pass.
        self.pass_it()
        self.assertIn('Article IV, Section 1', Section.objects.get(pk=self.secs[0].pk).content)


class NewSectionTests(_Base):

    def test_insert_renumbers_later_sections_only(self):
        self.add(kind='new_section', article_id=self.arts[2].pk, before_section_id=self.secs[1].pk,
                 title='Inserted', content='New text.')
        self.assertEqual(self.section_numbers(), [('1', 'Sec 1'), ('2', 'Sec 2'), ('3', 'Sec 3')])
        self.pass_it()
        self.assertEqual(self.section_numbers(), [('1', 'Sec 1'), ('2', 'Inserted'), ('3', 'Sec 2'), ('4', 'Sec 3')])
        self.assertEqual(Section.objects.get(article=self.arts[2], number='2').content, 'New text.')

    def test_a_section_in_a_new_article_of_the_same_resolution(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[1].pk, title='Standards')
        parent = ResolutionStructureChange.objects.get()
        self.add(kind='new_section', parent_id=parent.pk, title='Purpose', content='Why.')
        self.add(kind='new_section', parent_id=parent.pk, title='Scope', content='What.')
        self.pass_it()
        article = Article.objects.get(document=self.doc, title='Standards')
        self.assertEqual(article.number, 'II')
        self.assertEqual(self.section_numbers(article), [('1', 'Purpose'), ('2', 'Scope')])

    def test_needs_text(self):
        self.add(kind='new_section', article_id=self.arts[2].pk, before_section_id='', title='Empty', content='')
        self.assertFalse(ResolutionStructureChange.objects.exists())

    def test_non_numeric_section_blocks_an_insert_before_it(self):
        self.secs[2].number = '3a'
        self.secs[2].save()
        self.add(kind='new_section', article_id=self.arts[2].pk, before_section_id=self.secs[1].pk,
                 title='X', content='Y')
        self.assertFalse(ResolutionStructureChange.objects.exists())


class RenameTests(_Base):

    def test_rename_article_on_pass(self):
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='Executive Board')
        self.assertEqual(Article.objects.get(pk=self.arts[2].pk).title, 'Officers')
        change = ResolutionStructureChange.objects.get()
        self.assertEqual(change.old_title, 'Officers')
        self.pass_it()
        self.assertEqual(Article.objects.get(pk=self.arts[2].pk).title, 'Executive Board')

    def test_rename_section_on_pass_keeps_the_old_title_in_history(self):
        self.add(kind='rename_section', section_id=self.secs[0].pk, title='Composition')
        self.pass_it()
        self.assertEqual(Section.objects.get(pk=self.secs[0].pk).title, 'Composition')
        revision = SectionRevision.objects.get(section=self.secs[0])
        self.assertEqual((revision.title, revision.source), ('Sec 1', 'resolution'))

    def test_a_second_rename_of_the_same_thing_replaces_the_first(self):
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='One')
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='Two')
        self.assertEqual(list(ResolutionStructureChange.objects.values_list('title', flat=True)), ['Two'])

    def test_same_title_is_refused(self):
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='Officers')
        self.assertFalse(ResolutionStructureChange.objects.exists())


class PassedResolutionsKeepTheirCitationsTests(_Base):

    def test_an_amendment_keeps_the_number_it_was_voted_on_with(self):
        amendment = ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[0], proposed_text='Changed.',
            original_text_snapshot=self.secs[0].content, amendment_type='change')
        # The same resolution also inserts an article in front of it.
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[0].pk, title='Preamble')
        self.pass_it()
        amendment.refresh_from_db()
        self.assertEqual(amendment.identifier_snapshot, 'Bylaws Art. III § 1')
        self.assertEqual(amendment.display_identifier, 'Bylaws Art. III § 1')
        self.assertEqual(amendment.section.full_identifier, 'Bylaws Art. IV § 1')   # where it lives now
        html = self.client.get(reverse('cnb_resolution_print', args=[self.resolution.pk])).content.decode()
        self.assertIn('Bylaws Art. III § 1', html)

    def test_a_draft_amendment_follows_the_live_number(self):
        amendment = ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[0], proposed_text='Changed.',
            original_text_snapshot=self.secs[0].content, amendment_type='change')
        self.assertEqual(amendment.display_identifier, 'Bylaws Art. III § 1')


class SafetyTests(_Base):

    def test_a_pass_that_cannot_apply_rolls_back_whole(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        amendment = ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.secs[0], proposed_text='Changed.',
            original_text_snapshot=self.secs[0].content, amendment_type='change')
        # The document changes under the proposal after it was drafted.
        Article.objects.filter(pk=self.arts[3].pk).update(number='IV-A')
        self.pass_it()
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.status, 'pending')
        amendment.refresh_from_db()
        self.assertFalse(amendment.applied)
        self.assertNotEqual(Section.objects.get(pk=self.secs[0].pk).content, 'Changed.')
        self.assertFalse(Article.objects.filter(title='Standards').exists())

    def test_who_can_propose(self):
        url = reverse('cnb_add_structure_change', args=[self.resolution.pk])
        data = {'kind': 'rename_article', 'article_id': self.arts[2].pk, 'title': 'X'}
        self.client.force_login(self.member)
        self.client.post(url, data)
        self.assertFalse(ResolutionStructureChange.objects.exists())
        self.client.force_login(self.editor)
        self.client.post(url, data)
        self.assertEqual(ResolutionStructureChange.objects.count(), 1)

    def test_only_editors_can_remove_and_not_after_it_passed(self):
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='X')
        change = ResolutionStructureChange.objects.get()
        url = reverse('cnb_remove_structure_change', args=[self.resolution.pk, change.pk])
        self.client.force_login(self.member)
        self.client.post(url)
        self.assertTrue(ResolutionStructureChange.objects.exists())
        self.client.force_login(self.chair)
        self.pass_it()
        self.client.post(url)
        self.assertTrue(ResolutionStructureChange.objects.exists())

    def test_closed_resolution_takes_no_proposals(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='passed')
        self.add(kind='rename_article', article_id=self.arts[2].pk, title='X')
        self.assertFalse(ResolutionStructureChange.objects.exists())

    def test_pages_show_the_proposal(self):
        self.add(kind='new_article', document_id=self.doc.pk, before_article_id=self.arts[2].pk, title='Standards')
        for name in ('cnb_edit_resolution', 'cnb_resolution_detail', 'cnb_resolution_print'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name, args=[self.resolution.pk])).content.decode()
                self.assertIn('Bylaws Article III — Standards', html)
                self.assertIn('Current Articles III–IV become IV–V.', html)
