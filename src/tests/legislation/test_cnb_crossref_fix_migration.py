"""
09-27-26 — migration 0055_fix_cnb_cross_references corrects four C&B
cross-references on existing databases (prod). Pins: it fixes the old
wording, records the outgoing text as a SectionRevision, is idempotent, leaves
a section a resolution already reworded alone, and reverses cleanly.

Run with: python manage.py test src.tests.legislation.test_cnb_crossref_fix_migration
"""
import importlib
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase

from src.cnb_crossrefs import check
from src.models import Section, SectionRevision

mig = importlib.import_module('src.migrations.0055_fix_cnb_cross_references')


def _sec(doc, art, num):
    return Section.objects.get(article__document__doc_type=doc, article__number=art, number=num)


class CrossRefFixMigrationTests(TestCase):
    def setUp(self):
        call_command('seed_cnb_documents', stdout=StringIO())
        # Put the pre-fix wording back, as prod has it.
        for doc, art, sec, wrong, right in mig.FIXES:
            s = _sec(doc, art, sec)
            self.assertIn(right, s.content)
            s.content = s.content.replace(right, wrong)
            s.save()

    def test_fixes_all_three_sections_and_records_history(self):
        self.assertEqual(mig.apply_fixes(apps), 3)
        for doc, art, sec, wrong, right in mig.FIXES:
            s = _sec(doc, art, sec)
            self.assertIn(right, s.content)
            self.assertNotIn(wrong, s.content)
            rev = SectionRevision.objects.get(section=s)
            self.assertIn(wrong, rev.content)
            self.assertEqual(rev.source, 'direct_edit')

    def test_is_idempotent(self):
        mig.apply_fixes(apps)
        self.assertEqual(mig.apply_fixes(apps), 0)
        self.assertEqual(SectionRevision.objects.count(), 3)

    def test_leaves_reworded_sections_alone(self):
        s = _sec('bylaws', 'VI', '2')
        s.content = 'Reworded by a resolution.'
        s.save()
        self.assertEqual(mig.apply_fixes(apps), 2)
        self.assertEqual(_sec('bylaws', 'VI', '2').content, 'Reworded by a resolution.')

    def test_reverses(self):
        mig.apply_fixes(apps)
        self.assertEqual(mig.apply_fixes(apps, reverse=True), 3)
        self.assertIn(mig.FIXES[0][3], _sec('constitution', 'V', '1').content)

    def test_checker_is_clean_after_the_fix(self):
        mig.apply_fixes(apps)
        from src.cnb_crossrefs import Structure
        self.assertEqual(check(Structure.from_db()), [])
