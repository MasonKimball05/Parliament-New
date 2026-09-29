"""
Multi-chapter slice 3f (09-29-26) — restore_committees_and_roles matches by CODE.

It used to get_or_create(id=<n>) and overwrite the code and name of whatever
row had that id, which renames an unrelated committee on any database whose
ids don't line up with the defaults.

Run with: python manage.py test src.tests.infra.test_restore_committees_and_roles
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from src.chapter_structure import structure_problems
from src.models import Committee, Role


def run(*args):
    out = StringIO()
    call_command('restore_committees_and_roles', *args, stdout=out)
    return out.getvalue()


class RestoreByCodeTests(TestCase):
    def test_empty_database_gets_every_default(self):
        run()
        self.assertEqual(set(Committee.objects.values_list('code', flat=True)),
                         {c for _, c, _ in Committee.DEFAULT_COMMITTEES})
        self.assertEqual(set(Role.objects.values_list('code', flat=True)),
                         {c for _, c, _ in Role.DEFAULT_ROLES})
        kai = Committee.objects.get(code='KAI')
        self.assertTrue(kai.is_kai_committee)
        self.assertTrue(Committee.objects.get(code='EXEC').is_exec_board)

    def test_unrelated_row_with_a_default_id_is_not_touched(self):
        # The old bug: whatever sits at id 4 became "Kai Committee" / KAI.
        Committee.objects.create(id=4, code='HOUSING', name='Housing Committee')
        Role.objects.create(id=5, code='SCHOLAR', name='Scholarship Chair')
        run()
        self.assertEqual(Committee.objects.get(id=4).code, 'HOUSING')
        self.assertEqual(Committee.objects.get(id=4).name, 'Housing Committee')
        self.assertEqual(Role.objects.get(id=5).code, 'SCHOLAR')
        self.assertTrue(Committee.objects.filter(code='KAI').exists())
        self.assertTrue(Role.objects.filter(code='VPE').exists())

    def test_renamed_default_is_left_alone_unless_reset(self):
        run()
        Committee.objects.filter(code='KAI').update(name='Standards Board')
        run()
        self.assertEqual(Committee.objects.get(code='KAI').name, 'Standards Board')
        run('--reset-names')
        self.assertEqual(Committee.objects.get(code='KAI').name, 'Kai Committee')

    def test_name_taken_by_another_code_is_a_conflict_not_an_overwrite(self):
        Committee.objects.create(code='JUDICIAL', name='Kai Committee')
        out = run()
        self.assertIn("'Kai Committee' already exists with code 'JUDICIAL'", out)
        self.assertFalse(Committee.objects.filter(code='KAI').exists())
        self.assertEqual(Committee.objects.get(name='Kai Committee').code, 'JUDICIAL')

    def test_existing_flag_holder_is_not_doubled(self):
        Committee.objects.create(code='STANDARDS', name='Standards', is_kai_committee=True)
        run()
        self.assertEqual(Committee.objects.filter(is_kai_committee=True).count(), 1)
        self.assertFalse(Committee.objects.get(code='KAI').is_kai_committee)

    def test_dry_run_writes_nothing(self):
        out = run('--dry-run')
        self.assertIn('would create', out)
        self.assertEqual(Committee.objects.count(), 0)
        self.assertEqual(Role.objects.count(), 0)

    def test_idempotent(self):
        run()
        before = (Committee.objects.count(), Role.objects.count())
        run()
        self.assertEqual(before, (Committee.objects.count(), Role.objects.count()))

    def test_reports_what_is_still_missing(self):
        # CHAPTER isn't a default, so a fresh chapter still has to create it.
        out = run()
        self.assertIn('is_chapter_committee', out)
        self.assertTrue(any('is_chapter_committee' in p for p in structure_problems()))
