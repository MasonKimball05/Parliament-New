"""
Multi-chapter step 4a (09-29-26) — the Chapter table.

Pins:
  * the model's identity fields mirror src.chapter.ChapterIdentity exactly;
  * migrate seeds one default row that matches settings.CHAPTER;
  * at most one row can be the default (DB constraint);
  * src.W007 warns on drift and sync_chapter_from_settings fixes it;
  * get_chapter() is unchanged in 4a (still settings).

Run with: python manage.py test src.tests.infra.test_chapter_model
"""
from io import StringIO

from django.core.checks import run_checks
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from src.chapter import FIELD_NAMES, get_chapter
from src.models import Chapter


class ChapterModelTests(TestCase):
    def _w007(self):
        return [m for m in run_checks() if m.id == 'src.W007']

    def test_fields_mirror_chapter_identity(self):
        model_fields = {f.name for f in Chapter._meta.get_fields()}
        self.assertEqual(FIELD_NAMES - model_fields, set(), 'ChapterIdentity fields missing from Chapter')
        extra = model_fields - FIELD_NAMES - {'id', 'slug', 'is_default', 'is_active', 'created_at'}
        self.assertEqual(extra, set(), 'Chapter has identity-looking fields ChapterIdentity lacks')

    def test_migration_seeds_the_default_from_settings(self):
        row = Chapter.objects.default()
        self.assertIsNotNone(row)
        self.assertEqual(row.identity(), get_chapter())
        self.assertEqual(row.slug, 'alpha-mu')
        self.assertEqual(self._w007(), [])

    def test_only_one_default(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Chapter.objects.create(slug='other', is_default=True, domain='other.example',
                                   **{k: v for k, v in Chapter.values_from_settings().items() if k != 'domain'})

    def test_drift_warns_and_sync_fixes_it(self):
        Chapter.objects.filter(is_default=True).update(site_name='Old Name')
        warnings = self._w007()
        self.assertEqual(len(warnings), 1)
        self.assertIn('site_name', warnings[0].msg)
        out = StringIO()
        call_command('sync_chapter_from_settings', '--dry-run', stdout=out)
        self.assertIn("'Old Name'", out.getvalue())
        self.assertEqual(Chapter.objects.default().site_name, 'Old Name')   # dry run
        call_command('sync_chapter_from_settings', stdout=StringIO())
        self.assertEqual(Chapter.objects.default().site_name, get_chapter().site_name)
        self.assertEqual(self._w007(), [])

    def test_missing_default_flag_warns(self):
        Chapter.objects.update(is_default=False)
        self.assertIn('none is marked is_default', self._w007()[0].msg)

    def test_empty_table_is_quiet_and_sync_creates(self):
        Chapter.objects.all().delete()
        self.assertEqual(self._w007(), [])
        call_command('sync_chapter_from_settings', stdout=StringIO())
        self.assertEqual(Chapter.objects.default().identity(), get_chapter())

    def test_get_chapter_still_reads_settings_in_4a(self):
        from django.conf import settings
        Chapter.objects.filter(is_default=True).update(site_name='Table Name')
        with override_settings(CHAPTER={**settings.CHAPTER, 'site_name': 'Settings Name'}):
            self.assertEqual(get_chapter().site_name, 'Settings Name')

    def test_bad_lettering_anchor_fails_validation(self):
        from django.core.exceptions import ValidationError
        row = Chapter.objects.default()
        row.lettering_anchor = 'sometime = Zed'
        with self.assertRaises(ValidationError):
            row.full_clean()
