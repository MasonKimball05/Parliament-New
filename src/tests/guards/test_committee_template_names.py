"""
09-28-26 — committee template naming convention (Mason: "preferably the
education_ or recruitment_ instead of more ambiguous").

Every template the education or recruitment views render — and every partial
they include — is named for its committee:

    committee/education_*.html    committee/_education_*.html  (partials)
    committee/recruitment_*.html  committee/_recruitment_*.html

Renamed that day: education.html -> education_dashboard.html,
quiz_submissions.html -> education_quiz_submissions.html,
_quiz_analysis_body.html -> _education_quiz_analysis_body.html,
candidate_form.html -> recruitment_candidate_form.html,
candidate_list.html -> recruitment_candidate_list.html.

Generic committee pages shared by every committee (detail, vote, attendance,
documents, minutes...) keep their plain names — they are not education- or
recruitment-specific. `manage_*_permissions.html` keep their shared
`manage_<committee>_permissions` family name.

Run with: python manage.py test src.tests.guards.test_committee_template_names
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser

VIEWS = Path(settings.BASE_DIR) / 'src' / 'view' / 'committee'
TEMPLATES = Path(settings.BASE_DIR) / 'templates'
RENDERED = re.compile(r"""['"]committee/([\w.-]+\.html)['"]""")
INCLUDED = re.compile(r"""{%\s*include\s+['"]committee/([\w.-]+\.html)['"]""")
EXEMPT = {'manage_education_permissions.html', 'manage_recruitment_permissions.html'}


def _names_used_by(view_file):
    return set(RENDERED.findall((VIEWS / view_file).read_text(encoding='utf-8')))


class CommitteeTemplatesAreNamedForTheirCommitteeTests(TestCase):
    def _assert_prefixed(self, view_file, prefix):
        names = _names_used_by(view_file)
        self.assertTrue(names, f'no committee/ templates found in {view_file} — did the regex break?')
        # Partials included by those templates count too.
        for name in list(names):
            path = TEMPLATES / 'committee' / name
            self.assertTrue(path.exists(), f'{view_file} renders committee/{name}, which does not exist')
            names |= set(INCLUDED.findall(path.read_text(encoding='utf-8')))
        bad = sorted(n for n in names - EXEMPT
                     if not (n.startswith(f'{prefix}_') or n.startswith(f'_{prefix}_')))
        self.assertEqual(bad, [], f'{view_file} uses committee templates not named {prefix}_*.html')

    def test_education_templates(self):
        self._assert_prefixed('education.py', 'education')

    def test_recruitment_templates(self):
        self._assert_prefixed('recruitment.py', 'recruitment')

    def test_the_old_names_are_gone(self):
        for old in ('education.html', 'quiz_submissions.html', '_quiz_analysis_body.html',
                    'candidate_form.html', 'candidate_list.html'):
            self.assertFalse((TEMPLATES / 'committee' / old).exists(), old)


class RenamedRecruitmentTemplateRendersTests(TestCase):
    """The candidate form had no render test before the rename — this is it."""

    def test_create_candidate_form_renders(self):
        chair = ParliamentUser.objects.create_user(
            '9101', 'Rush Chair', 'rushchair', 'Active', password='Rush-Chair-Pass-9!')
        committee = Committee.objects.create(name='Recruitment', code='RCT',
                                             is_recruitment_committee=True)
        committee.chairs.add(chair)
        self.client.force_login(chair)
        r = self.client.get(reverse('create_candidate', kwargs={'code': 'RCT'}))
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, 'committee/recruitment_candidate_form.html')
