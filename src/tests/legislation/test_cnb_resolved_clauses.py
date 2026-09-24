"""
09-24-26 — multiple "be it resolved" clauses render as separate paragraphs.

Mason: two resolved clauses came out on ONE line in the Preview PDF. The
print and detail templates put all of `resolved_text` in a single <p>, so the
newline between clauses collapsed. `Resolution.resolved_clauses` now splits it
per line, the way whereas clauses always have been.

Run with: python manage.py test src.tests.legislation.test_cnb_resolved_clauses
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, Resolution

MASONS_TEXT = (
    'That the Executive Board of the Alpha Mu Chapter be reduced from nine (9) '
    'to seven (7) members; and\n'
    'Therefore be it further resolved, the positions of Vice President of '
    'Administration and Vice President of Risk Management be eliminated.'
)


class ResolvedClausesTests(TestCase):
    def _r(self, text):
        return Resolution(title='T', resolved_text=text)

    def test_one_line_keeps_the_original_prefix(self):
        self.assertEqual(
            self._r('the chapter adopt X.').resolved_clauses,
            [{'prefix': 'Therefore, be it resolved,', 'text': 'the chapter adopt X.'}],
        )

    def test_each_line_is_its_own_clause_and_later_lines_are_further_resolved(self):
        clauses = self._r('first\n\nsecond\nthird').resolved_clauses
        self.assertEqual([c['text'] for c in clauses], ['first', 'second', 'third'])
        self.assertEqual(clauses[0]['prefix'], 'Therefore, be it resolved,')
        self.assertEqual(clauses[1]['prefix'], 'Therefore, be it further resolved,')
        self.assertEqual(clauses[2]['prefix'], 'Therefore, be it further resolved,')

    def test_a_line_with_its_own_wording_is_not_prefixed_twice(self):
        """Existing resolutions already have the second opener typed by hand."""
        clauses = self._r(MASONS_TEXT).resolved_clauses
        self.assertEqual(len(clauses), 2)
        self.assertEqual(clauses[0]['prefix'], 'Therefore, be it resolved,')
        self.assertEqual(clauses[1]['prefix'], '')
        self.assertTrue(clauses[1]['text'].startswith('Therefore be it further resolved,'))

    def test_the_print_preview_renders_two_paragraphs(self):
        user = ParliamentUser.objects.create(
            user_id='CNB-RC1', username='CNB-RC1', name='CNB Writer',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        res = Resolution.objects.create(title='Board size', resolved_text=MASONS_TEXT, created_by=user)
        self.client.force_login(user)
        r = self.client.get(reverse('cnb_resolution_print', args=[res.pk]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertEqual(html.count('<p class="resolved-clause">'), 2)
        self.assertNotIn('members; and Therefore be it further', html)
