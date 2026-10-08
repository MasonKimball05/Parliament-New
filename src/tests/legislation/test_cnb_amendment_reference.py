"""
v3.45.0 (10-08-26) — how the print preview states an amendment.

Mason: "If something is just removed in a section edit have it say 'Shall
strike from x Article, x Section of the Constitution/Bylaws (Section Title)
(what is being struck)'. For amendments (additions or edits) 'Shall amend x
Article, x Section of the Constitution/Bylaws (Section title) (what is being
added if added, the before and then after if edited)', and some logic to
handle if/when there are multiple edits to 1 section."

Run with: python manage.py test src.tests.legislation.test_cnb_amendment_reference
"""
import json
import re

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from src.cnb_amendment_text import describe, edit_items, sentence
from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment, Section,
)

OFFICERS = ('2. The officers shall include:\n   a. President\n   b. EVP\n'
            '   c. VP Communication\n   d. VP Brotherhood')
DUES = 'Dues shall be paid within thirty days of the first chapter meeting of each semester.'


class EditItemsTests(SimpleTestCase):
    def kinds(self, original, proposed):
        return [item['kind'] for item in edit_items(original, proposed)]

    def test_a_removal_is_one_strike(self):
        items = edit_items(OFFICERS, OFFICERS.replace('   c. VP Communication\n', ''))
        self.assertEqual(items, [{'kind': 'strike', 'before': 'c. VP Communication', 'after': '', 'where': ''}])

    def test_an_addition_is_one_add(self):
        items = edit_items(OFFICERS, OFFICERS + '\n   e. VP Finance')
        self.assertEqual(items, [{'kind': 'add', 'before': '', 'after': 'e. VP Finance', 'where': 'at the end'}])

    def test_an_addition_says_where_it_goes(self):
        def where(proposed):
            return edit_items(DUES, proposed)[0]['where']
        self.assertEqual(where(DUES.replace('be paid', 'be paid in full')), 'after “Dues shall be paid”')
        self.assertEqual(where('Unless waived, ' + DUES), 'at the beginning')
        self.assertEqual(where(DUES + ' Late dues accrue a fee.'), 'at the end')

    def test_a_rewording_has_before_and_after(self):
        items = edit_items(DUES, DUES.replace('thirty', 'fourteen'))
        self.assertEqual(items, [{'kind': 'change', 'before': 'thirty', 'after': 'fourteen', 'where': ''}])

    def test_separate_edits_are_separate_items_in_document_order(self):
        proposed = (DUES.replace('Dues shall', 'All dues shall')
                        .replace('thirty', 'fourteen')
                        .replace(' of each semester', ''))
        self.assertEqual(self.kinds(DUES, proposed), ['change', 'change', 'strike'])
        self.assertEqual([i['before'] for i in edit_items(DUES, proposed)], ['Dues', 'thirty', 'of each semester'])

    def test_edits_two_words_apart_are_reported_as_one(self):
        items = edit_items(DUES, DUES.replace('thirty days of', 'fourteen days after'))
        self.assertEqual(items, [{'kind': 'change', 'before': 'thirty days of',
                                  'after': 'fourteen days after', 'where': ''}])

    def test_two_strikes_close_together_stay_two_strikes(self):
        items = edit_items(DUES, 'Dues shall be paid within thirty days of the first meeting.')
        self.assertEqual([(i['kind'], i['before']) for i in items],
                         [('strike', 'chapter'), ('strike', 'of each semester')])

    def test_striking_the_end_of_a_sentence_is_a_strike_not_a_change(self):
        items = edit_items(DUES, DUES.replace(' of each semester', ''))
        self.assertEqual([(i['kind'], i['before']) for i in items], [('strike', 'of each semester')])

    def test_line_endings_and_indentation_are_not_edits(self):
        self.assertEqual(edit_items(OFFICERS, OFFICERS.replace('\n   ', '\r\n\t')), [])

    def test_a_rewrite_is_one_change_of_the_whole_text(self):
        items = edit_items(DUES, 'The treasurer sets the schedule for payment every year.')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['kind'], 'change')
        self.assertEqual(items[0]['before'], DUES)

    def test_striking_everything_is_one_strike(self):
        self.assertEqual(edit_items(DUES, ''), [{'kind': 'strike', 'before': DUES, 'after': '', 'where': ''}])


class _Base(TestCase):
    def setUp(self):
        self.chair = ParliamentUser.objects.create(
            user_id='CNB-R1', name='Chair', username='cnbr1', member_type='Member',
            member_status='Active', is_admin=True, email='cnbr1@example.com')
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        document = GoverningDocument.objects.filter(doc_type='bylaws').first() \
            or GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws')
        self.article = Article.objects.create(document=document, number='XCIX', title='Test', display_order=999)
        self.section = Section.objects.create(article=self.article, number='3', title='Dues',
                                              content=DUES, display_order=1)

    def amend(self, proposed, section=None, **kwargs):
        section = section or self.section
        return ResolutionAmendment.objects.create(
            resolution=self.resolution, section=section, proposed_text=proposed,
            original_text_snapshot=section.content, **kwargs)


class SentenceTests(_Base):
    REF = 'Article XCIX, Section 3 of the Bylaws (Dues)'

    def test_only_removed_says_shall_strike_from(self):
        amendment = self.amend(DUES.replace(' of each semester', ''))
        self.assertEqual(sentence(amendment), f'Shall strike from {self.REF}: “of each semester”')
        self.assertEqual(describe(amendment)['action'], 'strike')

    def test_added_says_shall_amend_by_adding(self):
        amendment = self.amend(DUES + ' Late dues accrue a fee.')
        self.assertEqual(sentence(amendment), f'Shall amend {self.REF} by adding at the end: “Late dues accrue a fee.”')

    def test_edited_gives_before_then_after(self):
        amendment = self.amend(DUES.replace('thirty', 'fourteen'))
        self.assertEqual(sentence(amendment),
                         f'Shall amend {self.REF} by changing “thirty” to read “fourteen”')

    def test_several_edits_are_a_lettered_list_under_one_lead(self):
        amendment = self.amend(DUES.replace('Dues shall', 'All dues shall')
                                   .replace('thirty', 'fourteen')
                                   .replace(' of each semester', ''))
        self.assertEqual(sentence(amendment).split('\n'), [
            f'Shall amend {self.REF} as follows:',
            '(a) Change “Dues” to read “All dues”',
            '(b) Change “thirty” to read “fourteen”',
            '(c) Strike “of each semester”',
        ])
        self.assertEqual(describe(amendment)['action'], 'amend')

    def test_additions_in_a_list_say_where(self):
        amendment = self.amend(DUES.replace('be paid', 'be paid in full') + ' Late dues accrue a fee.')
        self.assertEqual(sentence(amendment).split('\n'), [
            f'Shall amend {self.REF} as follows:',
            '(a) Add after “Dues shall be paid”: “in full”',
            '(b) Add at the end: “Late dues accrue a fee.”',
        ])

    def test_several_removals_and_nothing_else_is_still_a_strike(self):
        amendment = self.amend('Dues shall be paid within thirty days of the first meeting.')
        said = describe(amendment)
        self.assertEqual(said['action'], 'strike')
        self.assertTrue(said['lead'].startswith('Shall strike from '))
        self.assertEqual(sentence(amendment).split('\n'), [
            f'Shall strike from {self.REF} as follows:',
            '(a) Strike “chapter”',
            '(b) Strike “of each semester”',
        ])

    def test_whole_section(self):
        amendment = self.amend('', amendment_type='deletion')
        said = describe(amendment)
        self.assertTrue(said['whole'])
        self.assertEqual(said['lead'], f'Shall strike {self.REF} in its entirety')

    def test_scope_note_follows_the_reference(self):
        amendment = self.amend(DUES.replace('thirty', 'fourteen'), scope_note='the deadline')
        self.assertTrue(sentence(amendment).startswith(f'Shall amend {self.REF} (the deadline) by changing'))

    def test_a_section_without_a_title_has_no_empty_brackets(self):
        bare = Section.objects.create(article=self.article, number='4', title='', content=DUES, display_order=2)
        amendment = self.amend(DUES.replace('thirty', 'fourteen'), section=bare)
        self.assertTrue(sentence(amendment).startswith('Shall amend Article XCIX, Section 4 of the Bylaws by'))

    def test_a_passed_resolution_keeps_the_numbers_it_was_voted_on_with(self):
        amendment = self.amend(DUES.replace('thirty', 'fourteen'),
                               applied=True, identifier_snapshot='Bylaws Art. XCIX § 3')
        Section.objects.filter(pk=self.section.pk).update(number='7')
        amendment.refresh_from_db()
        self.assertIn('Article XCIX, Section 3 of the Bylaws', describe(amendment)['reference'])


class PrintPageTests(_Base):
    def page(self):
        self.client.force_login(self.chair)
        response = self.client.get(reverse('cnb_resolution_print', args=[self.resolution.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def refs(self, html):
        blob = re.search(r'<script id="amendment-refs" type="application/json">(.*?)</script>', html, re.S)
        return json.loads(blob.group(1))

    def test_page_carries_one_reference_per_amendment_in_order(self):
        other = Section.objects.create(article=self.article, number='4', title='Fines', content=OFFICERS, display_order=2)
        self.amend(DUES.replace(' of each semester', ''))
        self.amend(OFFICERS + '\n   e. VP Finance', section=other)
        refs = self.refs(self.page())
        self.assertEqual([r['lead'] for r in refs], [
            'Shall strike from Article XCIX, Section 3 of the Bylaws (Dues)',
            'Shall amend Article XCIX, Section 4 of the Bylaws (Fines)',
        ])

    def test_markup_in_section_text_stays_data(self):
        self.amend(DUES + ' <b>bold</b>')
        html = self.page()
        self.assertNotIn('<b>bold</b>', html)
        self.assertEqual(self.refs(html)[0]['items'][0]['after'], '<b>bold</b>')

    def test_old_wording_is_gone(self):
        self.amend(DUES.replace('thirty', 'fourteen'))
        html = self.page()
        for old in ("'Removed: '", "'Added: '", 'The following shall be', 'the Alpha Mu of Beta Theta Pi, under'):
            self.assertNotIn(old, html)

    def test_no_amendments(self):
        self.assertEqual(self.refs(self.page()), [])
