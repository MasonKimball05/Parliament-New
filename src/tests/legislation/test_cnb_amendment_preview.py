"""
Amendment preview and editor fixes (v3.41.3, 10-02-26).

Mason, during a bylaws committee meeting: the preview showed an amendment that
removed one officer line as "2. a. b. d. e. f. ..." — only the list markers.
Cause: a textarea posts CRLF and section text is stored with LF, and the diff
compared tokens together with their leading whitespace, so the first word of
every line looked changed and the rest looked the same.

The diff itself is JavaScript (templates/cnb/resolution_print.html); what can
be pinned here is the server half and that the pages carry the fix.

Run with: python manage.py test src.tests.legislation.test_cnb_amendment_preview
"""
from django.test import TestCase
from django.urls import reverse

from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution, ResolutionAmendment, Section,
)

ORIGINAL = '2. The officers shall include:\n   a. President\n   b. EVP\n   c. VP Communication\n   d. VP Brotherhood'
CRLF_WITHOUT_C = '2. The officers shall include:\r\n   a. President\r\n   b. EVP\r\n   d. VP Brotherhood'


class AmendmentLineEndingTests(TestCase):
    def setUp(self):
        self.chair = ParliamentUser.objects.create(
            user_id='CNB-P1', name='Chair', username='cnbp1', member_type='Member',
            member_status='Active', is_admin=True)
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        document = GoverningDocument.objects.filter(doc_type='constitution').first() \
            or GoverningDocument.objects.create(doc_type='constitution', title='Constitution')
        article = Article.objects.create(document=document, number='XCIX', title='Test', display_order=999)
        self.section = Section.objects.create(article=article, number='3', title='Officers',
                                              content=ORIGINAL, display_order=1)
        self.client.force_login(self.chair)

    def _post(self, text):
        self.client.post(reverse('cnb_add_amendment', args=[self.resolution.pk]),
                         {'section_id': self.section.pk, 'proposed_text': text})
        return ResolutionAmendment.objects.get(resolution=self.resolution)

    def test_proposed_text_is_stored_with_lf(self):
        amendment = self._post(CRLF_WITHOUT_C)
        self.assertNotIn('\r', amendment.proposed_text)
        self.assertEqual(amendment.amendment_type, 'change')

    def test_an_addition_posted_with_crlf_is_typed_addition(self):
        amendment = self._post(ORIGINAL.replace('\n', '\r\n') + '\r\n   e. VP Finance')
        self.assertEqual(amendment.amendment_type, 'addition')

    def test_preview_compares_tokens_by_key_and_shows_removals(self):
        self._post(CRLF_WITHOUT_C)
        html = self.client.get(reverse('cnb_resolution_print', args=[self.resolution.pk])).content.decode()
        self.assertIn('function key(tok)', html)
        self.assertIn("'Removed: '", html)

    def test_editor_locks_the_page_behind_it(self):
        for name in ('cnb_edit_resolution', 'cnb_resolution_detail'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name, args=[self.resolution.pk])).content.decode()
                self.assertIn('MutationObserver', html)
                self.assertIn('height: 92vh; max-height: 92vh;', html)
