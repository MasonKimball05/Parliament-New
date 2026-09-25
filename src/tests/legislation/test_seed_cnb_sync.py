"""
09-25-26 — the C&B importer was built from the January 2025 text; prod was
missing Bylaws Art. III §5 (Sweethearts) and three sections were outdated.

Pins: the data now contains §5 and the August 2025 wording; a plain seed on an
existing install ADDS §5 without touching edited sections; `--only` + `--force`
replaces exactly the listed sections and nothing else.

Run with: python manage.py test src.tests.legislation.test_seed_cnb_sync
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from src.models import Section


def _sec(doc, art, num):
    return Section.objects.get(article__document__doc_type=doc, article__number=art, number=num)


def _seed(*args):
    call_command('seed_cnb_documents', *args, stdout=StringIO())


class SeedCnbSyncTests(TestCase):
    def setUp(self):
        _seed()

    def test_sweethearts_section_exists(self):
        s = _sec('bylaws', 'III', '5')
        self.assertEqual(s.title, 'Sweethearts')
        self.assertIn("A non-member position titled 'Sweetheart'", s.content)
        self.assertIn('Should 60% or more vote in favor of removal', s.content)

    def test_the_august_2025_wording_is_in_the_data(self):
        self.assertIn('Title IX of the Education Amendments of 1972', _sec('constitution', 'II', '3').content)
        self.assertIn('must meet with the Finance Committee', _sec('bylaws', 'IV', '2').content)
        self.assertIn('through the special meeting process', _sec('bylaws', 'VII', '10').content)

    def test_a_plain_seed_on_an_existing_install_adds_the_missing_section_only(self):
        """Prod's situation: §5 absent, the other sections already hold (older) text."""
        _sec('bylaws', 'III', '5').delete()
        old = _sec('constitution', 'II', '3')
        old.content = 'OLD JANUARY TEXT'
        old.save()
        _seed()
        self.assertTrue(Section.objects.filter(article__document__doc_type='bylaws',
                                               article__number='III', number='5').exists())
        self.assertEqual(_sec('constitution', 'II', '3').content, 'OLD JANUARY TEXT')

    def test_only_with_force_replaces_just_the_listed_sections(self):
        target = _sec('bylaws', 'IV', '2')
        target.content = 'OLD BUDGET TEXT'
        target.save()
        amended = _sec('bylaws', 'I', '1')           # stands in for a resolution-amended section
        amended.content = 'AMENDED BY RESOLUTION'
        amended.save()
        _seed('--force', '--only', 'bylaws:IV:2')
        self.assertIn('must meet with the Finance Committee', _sec('bylaws', 'IV', '2').content)
        self.assertEqual(_sec('bylaws', 'I', '1').content, 'AMENDED BY RESOLUTION')

    def test_only_rejects_a_key_that_is_not_in_the_data(self):
        with self.assertRaises(CommandError):
            _seed('--only', 'bylaws:III:9')
