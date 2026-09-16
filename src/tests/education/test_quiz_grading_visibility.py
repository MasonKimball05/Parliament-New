"""
v3.31.7 — two related requests from Mason about grading quizzes.

1. From `committee/EDUCATION/education/pledge/<pk>/` (the per-pledge detail
   page): *"can you make it for quizzes I can click on it and it shows the
   work they did and the answers so they can be 'graded'?"* The grading UI
   already existed on `education_quiz_submissions` (`quiz_submissions.html`)
   — per-question right/wrong marking, score entry, pass/incomplete — it was
   just never linked to from the one page a VPE actually opens to check on a
   specific pledge. Fixed with a "View answers →" link on each quiz-type
   task row, deep-linking to that pledge's own card via `#pledge-<pk>`.

2. From the main dashboard: *"I don't see a way to view all the test
   responses from the dashboard. Can you make it more apparent how to view
   this if it's there already?"* The submissions page WAS reachable, but
   only by hovering a task row for "Breakdown" (the item-analysis page) and
   then following ITS "← Submissions" back-link — a link worded and styled
   as an exit, not an entrance. Fixed with a persistent, always-visible
   "📝 View submissions" link on every quiz task row, not hidden behind
   row-hover like Duplicate/Questions/Breakdown/Delete are.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import PledgeTask, PledgeTaskQuestion, EducationMemberPermission
from src.tests.education._fixtures import EducationFixtureMixin, make_user


class DashboardSubmissionsLinkTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.plain_task = PledgeTask.objects.create(title='Plain Task')
        self.url = reverse('education_home', args=[self.committee.code])

    def test_a_quiz_task_shows_a_view_submissions_link(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn(
            reverse('education_quiz_submissions', args=[self.committee.code, self.quiz.pk]), html,
        )
        self.assertIn('View submissions', html)

    def test_a_non_quiz_task_shows_no_submissions_link(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn(
            reverse('education_quiz_submissions', args=[self.committee.code, self.plain_task.pk]), html,
        )

    def test_the_link_is_visible_to_a_granted_non_chair_officer(self):
        """
        ⚠️ v3.32.0 — REWRITTEN. Before v3.32.0 any chapter officer had full
        access to the education dashboard regardless of committee
        membership, so a bare "Officer" user could see this link with no
        further setup. v3.32.0 replaced that blanket rule with granular
        `EducationMemberPermission` grants (mirroring Kai, at Mason's
        direction) — a non-chair officer now sees NOTHING here without an
        explicit grant. This test asserts the link is still reachable once
        `can_view_submissions` is granted; the next test asserts it is NOT
        reachable without one.
        """
        officer = make_user('9006', 'Non-chair Officer', member_type='Officer')
        EducationMemberPermission.objects.create(
            committee=self.committee, user=officer, can_view_submissions=True,
        )
        client = Client()
        client.force_login(officer)
        html = client.get(self.url).content.decode()
        self.assertIn(
            reverse('education_quiz_submissions', args=[self.committee.code, self.quiz.pk]), html,
        )

    def test_an_ungranted_officer_is_denied_the_whole_page(self):
        """
        ⚠️ v3.32.0 — the actual behaviour change. Before this release, being
        ANY chapter officer was enough; now it is not — access is opt-in per
        `EducationMemberPermission` grant (or being a real committee chair,
        or a site admin).
        """
        officer = make_user('9008', 'Ungranted Officer', member_type='Officer')
        client = Client()
        client.force_login(officer)
        self.assertEqual(client.get(self.url).status_code, 404)

    def test_the_link_survives_for_a_plain_member_denied_the_page(self):
        """Not a visibility regression — the whole dashboard 404s first."""
        plain_member = make_user('9007', 'Plain Member', member_type='Member')
        client = Client()
        client.force_login(plain_member)
        self.assertEqual(client.get(self.url).status_code, 404)


class PledgeDetailAnswersLinkTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.plain_task = PledgeTask.objects.create(title='Plain Task')
        self.url = reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk])

    def test_the_quiz_row_links_to_that_pledges_answers(self):
        html = self.client.get(self.url).content.decode()
        expected = (
            reverse('education_quiz_submissions', args=[self.committee.code, self.quiz.pk])
            + f'#pledge-{self.pledge.pk}'
        )
        self.assertIn(expected, html)
        self.assertIn('View answers', html)

    def test_the_link_points_at_this_pledge_specifically_not_another_one(self):
        html = self.client.get(self.url).content.decode()
        other_pledge_anchor = f'#pledge-{self.other_pledge.pk}'
        self.assertNotIn(other_pledge_anchor, html)

    def test_a_non_quiz_task_gets_no_answers_link(self):
        # This page also has a quiz task (self.quiz), which legitimately DOES
        # show "View answers" on its own row -- so asserting the string is
        # absent from the whole page is the wrong scope. Assert instead that
        # the plain task's row specifically has no such link, by checking that
        # a submissions URL keyed to the plain task's pk (the only thing that
        # could ever justify a "View answers" link on its row) never appears.
        html = self.client.get(self.url).content.decode()
        plain_task_submissions_url = reverse(
            'education_quiz_submissions', args=[self.committee.code, self.plain_task.pk],
        )
        self.assertNotIn(plain_task_submissions_url, html)


class PledgeDetailNoQuizTaskAtAllTests(EducationFixtureMixin, TestCase):
    """A page with ONLY a non-quiz task should show no answers link at all."""

    def setUp(self):
        self.build()
        self.plain_task = PledgeTask.objects.create(title='Plain Task')
        self.url = reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk])

    def test_a_non_quiz_task_gets_no_answers_link(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('View answers', html)


class QuizSubmissionsPageHasPerPledgeAnchorsTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='What year was the fraternity founded?')
        self.url = reverse('education_quiz_submissions', args=[self.committee.code, self.quiz.pk])

    def test_every_pledge_gets_its_own_anchor(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn(f'id="pledge-{self.pledge.pk}"', html)
        self.assertIn(f'id="pledge-{self.other_pledge.pk}"', html)

    def test_no_inline_event_handlers(self):
        import re
        html = self.client.get(self.url).content.decode()
        self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\b(?:onclick|onchange|onsubmit)\s*=', html, re.IGNORECASE))
