"""
Multi-chapter slice 3e (09-29-26) — src.W006: the roles and committees the code
depends on exist, and each special committee flag is on exactly one committee.

Run with: python manage.py test src.tests.infra.test_chapter_structure
"""
from django.core.checks import run_checks
from django.test import TestCase

from src.chapter_structure import REQUIRED_COMMITTEE_FLAGS, REQUIRED_ROLE_CODES, structure_problems
from src.models import Committee, Role


def _seed_complete_chapter():
    for i, code in enumerate(REQUIRED_ROLE_CODES):
        Role.objects.create(code=code, name=f'Role {code}')
    for flag, (code, _) in REQUIRED_COMMITTEE_FLAGS.items():
        Committee.objects.create(code=code, name=f'Committee {code}', **{flag: True})


class ChapterStructureTests(TestCase):
    def _w006(self):
        return [m for m in run_checks() if m.id == 'src.W006']

    def test_empty_database_is_not_a_misconfigured_chapter(self):
        self.assertEqual(structure_problems(), [])
        self.assertEqual(self._w006(), [])

    def test_complete_chapter_is_quiet(self):
        _seed_complete_chapter()
        self.assertEqual(structure_problems(), [])
        self.assertEqual(self._w006(), [])

    def test_renaming_is_fine(self):
        _seed_complete_chapter()
        Role.objects.filter(code='VPP').update(name='VP of Service')
        Committee.objects.filter(is_kai_committee=True).update(name='Standards Board')
        self.assertEqual(structure_problems(), [])

    def test_missing_role_code_is_reported(self):
        _seed_complete_chapter()
        Role.objects.filter(code='VPP').delete()
        problems = structure_problems()
        self.assertEqual(len(problems), 1)
        self.assertIn("'VPP'", problems[0])
        self.assertIn('service-hours', problems[0])
        self.assertEqual(len(self._w006()), 1)

    def test_unflagged_kai_committee_is_reported(self):
        _seed_complete_chapter()
        Committee.objects.filter(is_kai_committee=True).update(is_kai_committee=False)
        self.assertTrue(any('is_kai_committee' in p for p in structure_problems()))

    def test_two_flagged_committees_are_reported(self):
        _seed_complete_chapter()
        Committee.objects.create(code='KAI2', name='Second Kai', is_kai_committee=True)
        problems = structure_problems()
        self.assertEqual(len(problems), 1)
        self.assertIn('2 committees have is_kai_committee=True', problems[0])

    def test_slating_committees_may_be_many(self):
        _seed_complete_chapter()
        Committee.objects.create(name='Slating A', is_slating_committee=True)
        Committee.objects.create(name='Slating B', is_slating_committee=True)
        self.assertEqual(structure_problems(), [])

    def test_exec_code_is_required_by_templates(self):
        _seed_complete_chapter()
        Committee.objects.filter(code='EXEC').update(code='BOARD')
        self.assertEqual(structure_problems(),
                         ["no committee with code 'EXEC' (templates link to it by code)"])
