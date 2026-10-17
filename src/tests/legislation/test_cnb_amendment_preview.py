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
        # v3.45.0 — the "Removed: / Added:" labels became the reference
        # sentence (src/cnb_amendment_text.py); the removal is in its items.
        self.assertIn('Shall strike from Article XCIX, Section 3 of the Constitution (Officers)', html)
        self.assertIn('c. VP Communication', html)

    def test_editor_locks_the_page_behind_it(self):
        for name in ('cnb_edit_resolution', 'cnb_resolution_detail'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name, args=[self.resolution.pk])).content.decode()
                self.assertIn('MutationObserver', html)
                self.assertIn('height: 92vh; max-height: 92vh;', html)


class AutosaveBeforeAmendmentTests(AmendmentLineEndingTests):
    """
    v3.41.4 — saving an amendment reloads the edit page; with unsaved edits in
    the main form the browser asked "leave site?". The page now saves the main
    form first. The behaviour is JavaScript; this pins that the edit page is
    wired for it and the create page is not (its URL would create a resolution).
    """
    test_proposed_text_is_stored_with_lf = None
    test_an_addition_posted_with_crlf_is_typed_addition = None
    test_preview_compares_tokens_by_key_and_shows_removals = None
    test_editor_locks_the_page_behind_it = None

    def test_edit_page_is_wired_and_create_page_is_not(self):
        edit = self.client.get(reverse('cnb_edit_resolution', args=[self.resolution.pk])).content.decode()
        self.assertIn('id="resolutionForm" data-autosave="1"', edit)
        create = self.client.get(reverse('cnb_create_resolution')).content.decode()
        self.assertIn('id="resolutionForm"', create)
        self.assertNotIn('data-autosave="1"', create)


class EditPageQueryCountTests(TestCase):
    """v3.41.5 — the edit page must not cost queries per amendment."""

    def _page_queries(self, amendment_count):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        chair = ParliamentUser.objects.create(
            user_id=f'CNB-Q{amendment_count}', name='Chair', username=f'cnbq{amendment_count}',
            member_type='Member', member_status='Active', is_admin=True, email=f'q{amendment_count}@example.com')
        resolution = Resolution.objects.create(title='T', created_by=chair)
        document = GoverningDocument.objects.filter(doc_type='constitution').first() \
            or GoverningDocument.objects.create(doc_type='constitution', title='Constitution')
        for n in range(amendment_count):
            article = Article.objects.create(document=document, number=f'Q{amendment_count}-{n}',
                                             title='A', display_order=900 + n)
            section = Section.objects.create(article=article, number='1', title='S', content='x', display_order=1)
            ResolutionAmendment.objects.create(resolution=resolution, section=section, proposed_text='y',
                                               original_text_snapshot='x', amendment_type='change')
        self.client.force_login(chair)
        url = reverse('cnb_edit_resolution', args=[resolution.pk])
        self.client.get(url)   # warm caches (flags, session)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_query_count_does_not_grow_with_amendments(self):
        self.assertEqual(self._page_queries(2), self._page_queries(8))


class DetailPageQueryCountTests(TestCase):
    """v3.42.1 — the resolution page must not cost queries per amendment."""

    def _page_queries(self, amendment_count):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        chair = ParliamentUser.objects.create(
            user_id=f'CNB-D{amendment_count}', name='Chair', username=f'cnbd{amendment_count}',
            member_type='Member', member_status='Active', is_admin=True, email=f'd{amendment_count}@example.com')
        resolution = Resolution.objects.create(title='T', created_by=chair)
        document = GoverningDocument.objects.filter(doc_type='constitution').first() \
            or GoverningDocument.objects.create(doc_type='constitution', title='Constitution')
        for n in range(amendment_count):
            article = Article.objects.create(document=document, number=f'D{amendment_count}-{n}',
                                             title='A', display_order=800 + n)
            section = Section.objects.create(article=article, number='1', title='S', content='x', display_order=1)
            Section.objects.create(article=article, number='2', title='S2', content='y', display_order=2)
            ResolutionAmendment.objects.create(resolution=resolution, section=section, proposed_text='z',
                                               original_text_snapshot='x', amendment_type='change')
        self.client.force_login(chair)
        url = reverse('cnb_resolution_detail', args=[resolution.pk])
        self.client.get(url)   # warm caches (flags, session)
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_query_count_does_not_grow_with_amendments(self):
        self.assertEqual(self._page_queries(2), self._page_queries(8))
