"""
A pledge-facing task view must ask whether the task is any of his business
(v3.21.5).

⚠️ WHAT WAS FOUND. `pledge_take_quiz` has always applied two predicates before
rendering anything: the task must be **live** (its `activation_mode` satisfied)
and, if it carries an explicit `assigned_to` list, this pledge must be on it.
`pledge_quiz_analysis` — added in v3.21.0, four months later, reading the same
model — applied neither. It checked `show_analysis_to_pledges` and stopped.

Two things were reachable as a result:

  * the breakdown for a quiz assigned to one specific pledge, by any other
    pledge; and
  * the breakdown for an **unpublished draft**, which matters more than it
    sounds, because `education_duplicate_task` copies `show_analysis_to_pledges`
    onto a clone whose whole purpose is to be invisible until the chair
    publishes it — and this page renders every question's text.

⚠️ IT WAS A REAL DISCLOSURE, AND THE FIRST DRAFT OF THIS DOCSTRING SAID IT WAS
NOT. I reasoned that a draft has no submissions, so v3.21.5's
minimum-submissions threshold would empty the rows before they rendered — then
ran these tests against `f241f45` and `test_the_question_text_does_not_reach_the_
page` **failed**. Of course it did: that tree has no threshold either. I had
reasoned about shipped code using a protection introduced in the same release I
was writing.

This codebase has the same mistake on file. v3.19.3 gave draft attachments uuid
filenames and wrote four separate comments saying the uuid was defence in depth
and explicitly *not* the access control — and then the uuid was the access
control for two days, because the old route was never closed.

> **An incidental protection is not a control**, and a protection you added
> yourself five minutes ago is not evidence about the code you are reviewing.
> Run the control; do not derive it.

So the two predicates are one function, and `TheEntitlementCheckIsShared` fails
the build if either view stops calling it — tenth instance of the shape CLAUDE.md
tracks, and the response has to be structural or there will be an eleventh.
"""
import inspect

from django.test import TestCase
from django.urls import reverse

from src.models import PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion, PledgeQuizAnswer
from src.test_education_scoring_and_meetings import EducationFixtureMixin, make_user
from src.view import pledge_tasks


class PledgeQuizEntitlementTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.question_text = 'What are the three Great Principles?'

    def _quiz(self, **kwargs):
        defaults = dict(
            title='Founders Quiz', task_type='quiz', is_active=True,
            show_analysis_to_pledges=True, activation_mode='immediate',
        )
        defaults.update(kwargs)
        task = PledgeTask.objects.create(**defaults)
        question = PledgeTaskQuestion.objects.create(
            task=task, question_text=self.question_text, display_order=1,
        )
        return task, question

    def _fill_to_threshold(self, task, question):
        """Three submissions, so the anonymity threshold is not what is
        withholding the page. Without this every assertion below would pass for
        the wrong reason."""
        for index in range(3):
            pledge = make_user(f'P-EN{index:04d}', f'Pledge {index}', member_type='Pledge')
            PledgeTaskCompletion.objects.create(task=task, pledge=pledge, score=5)
            PledgeQuizAnswer.objects.create(
                question=question, pledge=pledge, answer_text='x', is_correct=True,
            )

    def _as_pledge(self, url):
        self.client.force_login(self.other_pledge)
        return self.client.get(url)

    # -- the two predicates ------------------------------------------------

    def test_an_unpublished_draft_is_not_readable(self):
        task, question = self._quiz(activation_mode='manual', is_published=False)
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertEqual(response.status_code, 404)

    def test_a_quiz_assigned_to_somebody_else_is_not_readable(self):
        task, question = self._quiz()
        task.assigned_to.set([self.pledge])
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertEqual(response.status_code, 404)

    def test_a_timed_quiz_that_has_not_opened_is_not_readable(self):
        from datetime import timedelta

        from django.utils import timezone

        task, question = self._quiz(
            activation_mode='timed', activates_at=timezone.now() + timedelta(days=7),
        )
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertEqual(response.status_code, 404)

    def test_the_question_text_does_not_reach_the_page(self):
        """
        The assertion that matters, and the one a status-code check cannot make.
        A 404 is only interesting because of what it withholds.
        """
        task, question = self._quiz(activation_mode='manual', is_published=False)
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertNotIn(self.question_text, response.content.decode())

    # -- the controls ------------------------------------------------------

    def test_a_live_unassigned_quiz_is_still_readable(self):
        """
        CONTROL. "Assigned to nobody" means "assigned to everybody" — the common
        case — and an entitlement check that closed this would have deleted the
        feature rather than secured it.
        """
        task, question = self._quiz()
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.question_text, response.content.decode())

    def test_a_quiz_assigned_to_this_pledge_is_readable(self):
        """CONTROL for the other half of the same predicate."""
        task, question = self._quiz()
        task.assigned_to.set([self.other_pledge])
        self._fill_to_threshold(task, question)

        response = self._as_pledge(reverse('pledge_quiz_analysis', args=[task.pk]))

        self.assertEqual(response.status_code, 200)

    def test_the_educator_is_unaffected_by_a_draft(self):
        """
        CONTROL. A chair looking at his own unpublished draft is the entire
        point of a draft.
        """
        task, question = self._quiz(activation_mode='manual', is_published=False)

        self.client.force_login(self.chair)
        response = self.client.get(
            reverse('education_quiz_analysis', args=[self.committee.code, task.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(self.question_text, response.content.decode())

    def test_taking_a_quiz_is_still_gated_the_same_way(self):
        """
        CONTROL for the refactor itself: `pledge_take_quiz` had these checks
        already, and moving them into a shared function must not have changed
        what it does.
        """
        task, _ = self._quiz(activation_mode='manual', is_published=False)

        self.client.force_login(self.other_pledge)
        response = self.client.get(reverse('pledge_take_quiz', args=[task.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'pledge/quiz_not_available.html')


class TheEntitlementCheckIsShared(TestCase):
    """
    Structural, because the drift this guards against is invisible in behaviour
    until somebody adds a third pledge-facing view — which is exactly how the
    second one came to be missing it.
    """

    def test_both_pledge_facing_task_views_call_the_predicate(self):
        for view in (pledge_tasks.pledge_take_quiz, pledge_tasks.pledge_quiz_analysis):
            with self.subTest(view=view.__name__):
                self.assertIn(
                    'pledge_may_see_task', inspect.getsource(view),
                    f'{view.__name__} renders a PledgeTask to a pledge without '
                    f'asking whether he is entitled to see it. Two predicates '
                    f'(live, assigned) live in pledge_may_see_task; call it '
                    f'rather than re-deriving them.'
                )

    def test_the_predicate_is_not_satisfied_by_the_sharing_flag_alone(self):
        """
        ⚠️ The two questions are different and must both be asked.
        `show_analysis_to_pledges` asks whether the chair shared the breakdown;
        `pledge_may_see_task` asks whether the quiz is this pledge's business at
        all. v3.21.0 asked only the first. This pins that the analysis view
        still asks both, by name, so a future tidy-up cannot collapse them.
        """
        source = inspect.getsource(pledge_tasks.pledge_quiz_analysis)
        self.assertIn('pledge_may_see_task', source)
        self.assertIn('show_analysis_to_pledges', source)
