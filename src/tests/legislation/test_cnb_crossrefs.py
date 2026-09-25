"""
09-25-26 — C&B cross-reference checker (src/cnb_crossrefs.py).

Pins the four problems found in the August 2025 text, the linking, and that
the chair sees the findings. Fixing the WORDING is a chapter resolution; when
one passes, update KNOWN_PROBLEMS here (a shrinking list, like KNOWN_ORPHANS).

Run with: python manage.py test src.tests.legislation.test_cnb_crossrefs
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from src.cnb_crossrefs import Structure, check, find_references, link_references
from src.models import ParliamentUser

#: (level, source, reference text) — present in the seeded August 2025 text.
KNOWN_PROBLEMS = {
    ('error', 'Constitution V §1', 'Article VII, Section 1'),
    ('error', 'Bylaws VII §10', 'Article VII of the Constitution'),
    ('warning', 'Bylaws VII §10', 'Article VI of the Bylaws'),
    ('warning', 'Bylaws VI §2', 'Article VI, Section 1 (a) of the Bylaws'),
}


def _structure(docs):
    return Structure(docs)


TOY = [
    ('constitution', 'Constitution', [
        ('I', 'Name', [('1', 'Name', 'See Article II of the Bylaws.')]),
        ('VI', 'Amendments to the Constitution', [('1', 'Process', 'x')]),
    ]),
    ('bylaws', 'Bylaws', [
        ('II', 'Officers', [('1', 'Duties', 'As in Article I, Section 1 of the Constitution.')]),
        ('VI', 'Executive Board Expectations', [('1', 'Expectations', 'x')]),
        ('X', 'Amendments to the Bylaws', [('1', 'Process',
            'For amendments to the Bylaws see Article VI of the Bylaws. Also Article IX, Section 2.')]),
    ]),
]


class ParserTests(SimpleTestCase):
    def test_explicit_and_implicit_document(self):
        refs = find_references('per Article II, Section 2 (6) of the Bylaws and Article IV', 'constitution')
        self.assertEqual([(r.doc, r.art, r.sec) for r in refs], [('bylaws', 'II', '2'), ('constitution', 'IV', '')])

    def test_bracketed_builder_form(self):
        refs = find_references('see [Bylaws Art. VII § 1]', 'constitution')
        self.assertEqual([(r.doc, r.art, r.sec) for r in refs], [('bylaws', 'VII', '1')])

    def test_missing_target_is_an_error_and_topic_mismatch_a_warning(self):
        found = {(f.level, f.source, f.ref_text) for f in check(_structure(TOY))}
        self.assertIn(('error', 'Bylaws X §1', 'Article IX, Section 2'), found)
        self.assertIn(('warning', 'Bylaws X §1', 'Article VI of the Bylaws'), found)
        self.assertNotIn(('error', 'Constitution I §1', 'Article II of the Bylaws'), found)

    def test_links_escape_and_skip_missing_targets(self):
        st = _structure(TOY)
        html = link_references('<b>x</b> Article II of the Bylaws, Article IX, Section 2', 'bylaws', st)
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', html)
        self.assertIn('href="#bylaws-art-II"', html)
        self.assertNotIn('bylaws-art-IX', html)


class SeededDocumentTests(TestCase):
    def setUp(self):
        call_command('seed_cnb_documents', stdout=StringIO())

    def test_the_known_problems_are_exactly_what_is_found(self):
        found = {(f.level, f.source, f.ref_text) for f in check(Structure.from_db())}
        self.assertEqual(found, KNOWN_PROBLEMS,
                         'If a resolution fixed one of these, remove it from KNOWN_PROBLEMS. '
                         'If a new one appeared, a section was renumbered or a reference mistyped.')

    def test_command_strict_fails_on_errors(self):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('check_cnb_references', '--strict', stdout=out)
        self.assertIn('Constitution V §1', out.getvalue())

    def test_the_chair_sees_findings_on_the_manage_tab(self):
        chair = ParliamentUser.objects.create(user_id='XR-1', username='xr1', name='CNB Chair',
                                              member_type='Officer', member_status='Active', is_admin=True)
        c = Client(); c.force_login(chair)
        html = c.get(reverse('constitution_bylaws'), {'tab': 'manage'}).content.decode()
        self.assertIn('Cross-reference problems (4)', html)

    def test_the_viewer_links_valid_references_to_their_section(self):
        member = ParliamentUser.objects.create(user_id='XR-2', username='xr2', name='Member',
                                               member_type='Member', member_status='Active')
        c = Client(); c.force_login(member)
        r = c.get(reverse('constitution_bylaws'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('href="#bylaws-art-VII-sec-1"', html)       # a valid "Article VII, Section 1 ... of the Bylaws"
        self.assertNotIn('href="#constitution-art-VII', html)      # the broken one stays plain text

    def test_a_member_does_not_see_the_findings_panel(self):
        member = ParliamentUser.objects.create(user_id='XR-3', username='xr3', name='Member 3',
                                               member_type='Member', member_status='Active')
        c = Client(); c.force_login(member)
        html = c.get(reverse('constitution_bylaws'), {'tab': 'manage'}).content.decode()
        self.assertNotIn('Cross-reference problems', html)
