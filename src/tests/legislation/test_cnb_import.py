"""
09-27-26 — multi-chapter phase 2: the C&B is chapter CONTENT, loaded from
`chapter_content/<chapter>/` (src/chapter_content.py), not a hardcoded module.

Pins:
  * the default source is Alpha Mu's and still seeds exactly as before;
  * CHAPTER_CONTENT_DIR / --source select another chapter's JSON;
  * a malformed source is refused BEFORE anything is written, with every
    problem listed;
  * a .py source outside the repo's chapter_content/ is refused (it would be
    executed);
  * export_cnb_documents → seed_cnb_documents --source round-trips.

Run with: python manage.py test src.tests.legislation.test_cnb_import
"""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from src.chapter_content import ChapterContentError, load_cnb_documents, validate_cnb_documents
from src.models import Article, GoverningDocument, Section

TINY = [{
    'doc_type': 'constitution', 'title': 'Constitution of Gamma Delta', 'display_order': 1,
    'amendment_protection_weeks': 4, 'preamble': 'We, the members…',
    'articles': [{'number': 'I', 'title': 'Name', 'sections': [
        {'number': '1', 'title': 'Name', 'content': 'The name shall be Gamma Delta.'},
        {'number': '2', 'title': 'Old rule', 'content': 'Suspended.', 'is_active': False},
    ]}],
}]


def _seed(*args):
    call_command('seed_cnb_documents', *args, stdout=StringIO(), stderr=StringIO())


class DefaultSourceTests(TestCase):
    def test_default_source_is_the_original_chapters_and_validates(self):
        docs = load_cnb_documents()
        self.assertTrue({'constitution', 'bylaws'} <= {d['doc_type'] for d in docs})

    def test_old_module_path_is_gone(self):
        with self.assertRaises(ImportError):
            __import__('src.management.data.cnb_data')


class OtherChapterSourceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / 'cnb.json').write_text(json.dumps({'documents': TINY}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_content_dir_setting_selects_the_chapter(self):
        with override_settings(CHAPTER_CONTENT_DIR=self.dir):
            _seed()
        doc = GoverningDocument.objects.get()
        self.assertEqual(doc.title, 'Constitution of Gamma Delta')
        self.assertEqual(doc.amendment_protection_weeks, 4)
        s1, s2 = Section.objects.order_by('number')
        self.assertTrue(s1.is_active)
        self.assertFalse(s2.is_active)

    def test_source_flag_overrides(self):
        _seed('--source', str(self.dir / 'cnb.json'))
        self.assertEqual(Article.objects.get().title, 'Name')

    def test_missing_source_is_a_clear_error(self):
        with override_settings(CHAPTER_CONTENT_DIR=self.dir / 'nope'):
            with self.assertRaisesRegex(CommandError, 'looked for cnb.json and cnb.py'):
                _seed()


class ValidationTests(TestCase):
    def test_every_problem_is_reported_and_nothing_is_written(self):
        bad = [{'doc_type': 'manifesto', 'title': '', 'articles': [
            {'number': 'I', 'title': 'A', 'sections': [
                {'number': '1', 'content': ''}, {'number': '1', 'content': 'x', 'colour': 'red'}]},
            {'number': 'I', 'title': ''},
        ]}]
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(bad, f)
        with self.assertRaises(CommandError) as cm:
            _seed('--source', f.name)
        msg = str(cm.exception)
        for fragment in ('doc_type must be one of', 'title is required', 'content is required',
                         "§ 1: appears twice", "unknown key 'colour'", 'Art. I: appears twice'):
            self.assertIn(fragment, msg)
        self.assertFalse(GoverningDocument.objects.exists())
        Path(f.name).unlink()

    def test_py_source_outside_chapter_content_is_refused(self):
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
            f.write('raise SystemExit("this must never run")\nDOCUMENTS = []\n')
        with self.assertRaisesRegex(ChapterContentError, 'must live inside'):
            load_cnb_documents(f.name)
        Path(f.name).unlink()

    def test_a_prose_only_document_needs_its_preamble(self):
        with self.assertRaisesRegex(ChapterContentError, 'neither articles nor preamble'):
            validate_cnb_documents([{'doc_type': 'foreword', 'title': 'Foreword', 'preamble': ' '}])


class ExportRoundTripTests(TestCase):
    def test_export_then_import_reproduces_the_documents(self):
        _seed()
        # Simulate a resolution amendment and a ruling, so the export is not
        # just the seed data again.
        sec = Section.objects.filter(article__document__doc_type='bylaws').first()
        sec.content = 'Amended text.'
        sec.save()
        other = Section.objects.exclude(pk=sec.pk).filter(article__document__doc_type='constitution').first()
        other.is_active = False
        other.save()

        def snapshot():
            return sorted(
                (s.article.document.doc_type, s.article.number, s.number, s.title, s.content, s.is_active)
                for s in Section.objects.select_related('article__document'))
        docs_before = sorted(GoverningDocument.objects.values_list('doc_type', 'title', 'preamble',
                                                                   'amendment_protection_weeks'))
        before = snapshot()

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'cnb.json'
            call_command('export_cnb_documents', '-o', str(out), stdout=StringIO(), stderr=StringIO())
            GoverningDocument.objects.all().delete()
            self.assertFalse(Section.objects.exists())
            _seed('--source', str(out))

        self.assertEqual(snapshot(), before)
        self.assertEqual(sorted(GoverningDocument.objects.values_list(
            'doc_type', 'title', 'preamble', 'amendment_protection_weeks')), docs_before)

    def test_export_with_nothing_to_export_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command('export_cnb_documents', stdout=StringIO())
