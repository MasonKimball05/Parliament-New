"""
09-28-26 — multi-chapter: a chapter's OWN reference documents (its ratified C&B
PDF) come from chapter content, not from a literal in view_document.py.

Pins:
  * the original chapter still gets its C&B PDF entry, with the same title;
  * a chapter with no reference_documents.json has no C&B entry: the viewer
    404s for the slug and /constitution-bylaws/ hides the "Official PDF" link;
  * fraternity-wide documents are there for every chapter;
  * the loader rejects paths that could leave MEDIA_ROOT, and unknown keys.

Run with: python manage.py test src.tests.documents.test_reference_documents
"""
import json
import os
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from src.chapter_content import ChapterContentError, load_reference_documents
from src.models import ParliamentUser
from src.view.view_document import get_reference_documents


class ReferenceDocumentsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.empty_chapter = Path(self.tmp.name)
        self.user = ParliamentUser.objects.create_user(
            'refdoc1', 'Ref Doc', 'refdoc', 'Active', password='Ref-Doc-Test-Pass-9!')
        self.client.force_login(self.user)

    def test_original_chapter_keeps_its_cnb_pdf(self):
        doc = get_reference_documents()['constitution-bylaws']
        self.assertEqual(doc['title'], 'Constitution & Bylaws of the Samford Chapter')
        self.assertTrue(doc['path'].startswith('legislation_docs/'))
        self.assertEqual(doc['back_url_name'], 'constitution_bylaws')

    def test_fraternity_documents_are_shared(self):
        with override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
            docs = get_reference_documents()
        self.assertIn('code-of-beta', docs)
        self.assertEqual(docs['code-of-beta']['title'], 'Code of Beta Theta Pi (44th Edition)')
        self.assertNotIn('constitution-bylaws', docs)

    def test_chapter_without_a_cnb_pdf_404s_and_hides_the_link(self):
        with override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
            r = self.client.get(reverse('view_reference_document', args=['constitution-bylaws']))
            self.assertEqual(r.status_code, 404)
            page = self.client.get(reverse('constitution_bylaws'))
        self.assertEqual(page.status_code, 200)
        # The link sits in the document sidebar, which only renders when
        # governing documents exist, so the flag is asserted, not the HTML.
        self.assertIs(page.context['has_official_cnb_pdf'], False)

    def test_original_chapter_shows_the_link(self):
        page = self.client.get(reverse('constitution_bylaws'))
        self.assertIs(page.context['has_official_cnb_pdf'], True)

    def _write(self, data):
        (self.empty_chapter / 'reference_documents.json').write_text(json.dumps(data))

    def test_loader_rejects_escaping_paths_and_unknown_keys(self):
        for bad in ({'x': {'path': '../settings.py'}},
                    {'x': {'path': os.path.join(os.sep, 'abs', 'doc.pdf')}},
                    {'x': {'path': 'a.pdf', 'oops': 1}},
                    {'Bad Slug': {'path': 'a.pdf'}},
                    {'x': {}}):
            self._write(bad)
            with self.subTest(bad=bad), override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
                with self.assertRaises(ChapterContentError):
                    load_reference_documents()

    def test_loader_rejects_titles_that_would_500_the_page(self):
        # 09-29-26 (slice 3e): get_reference_documents() .format()s these.
        for bad in ({'x': {'path': 'a.pdf', 'title': 5}},
                    {'x': {'path': 'a.pdf', 'title': 'Bylaws {2025}'}},
                    {'x': {'path': 'a.pdf', 'description': 'The {chapter} rules'}},
                    {'x': {'path': 'a.pdf', 'title': 'unbalanced {'}}):
            self._write(bad)
            with self.subTest(bad=bad), override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
                with self.assertRaises(ChapterContentError):
                    load_reference_documents()
        self._write({'x': {'path': 'a.pdf', 'title': 'Bylaws {{2025}} of {school_short}'}})
        with override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
            self.assertEqual(get_reference_documents()['x']['title'], 'Bylaws {2025} of Samford')

    def test_bad_file_is_a_startup_error_not_a_500(self):
        # 09-29-26 (slice 3e): src.E001 now also loads reference_documents.json.
        from src.checks_platform import chapter_config_is_valid
        with override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
            self.assertEqual(chapter_config_is_valid(None), [])   # no file: fine
            (self.empty_chapter / 'reference_documents.json').write_text('{not json')
            errors = chapter_config_is_valid(None)
        self.assertEqual([e.id for e in errors], ['src.E001'])
        self.assertIn('reference-PDF', errors[0].msg)

    def test_chapter_title_override(self):
        self._write({'constitution-bylaws': {'path': 'legislation_docs/x.pdf', 'title': 'Our Charter'}})
        with override_settings(CHAPTER_CONTENT_DIR=self.empty_chapter):
            doc = get_reference_documents()['constitution-bylaws']
        self.assertEqual(doc['title'], 'Our Charter')
        self.assertIn('Chapter of Beta Theta Pi', doc['description'])


class SeedResolutionsIsOriginalChapterOnlyTests(TestCase):
    @override_settings(CHAPTER_IS_DEFAULT=False)
    def test_refuses_for_another_chapter(self):
        from django.core.management import CommandError, call_command
        with self.assertRaises(CommandError):
            call_command('seed_resolutions')
