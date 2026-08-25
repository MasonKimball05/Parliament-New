"""
Each number on the quiz breakdown is gated by its OWN population (v3.21.7).

⚠️ WHAT WAS WRONG. v3.21.5 added an anonymity minimum to the pledge-facing quiz
breakdown, for a good reason: with one submission, "class totals" were that one
pledge's exact score and right/wrong pattern, printed under a header promising
nobody's individual answers are shown.

The threshold it added counted `PledgeTaskCompletion` rows. **A completion is
not a submission.** `education_toggle_completion` calls `get_or_create`, so a
chair marking somebody `waived` or `incomplete` on the grid mints a row with no
answers behind it. Measured on `d7f925f` — one pledge sits the quiz, the chair
marks two others, and the pledge-facing page renders:

    submissions = 3 | withheld = False
    Q1  answered=1  correct=1  wrong=0  100%
    Q2  answered=1  correct=0  wrong=1    0%
    score_count = 1   low/high/avg = 4 4 4.0

That is the disclosure the threshold exists to prevent, one day later, reached
by an ordinary chair action rather than by an attack.

> **A THRESHOLD PROTECTS THE POPULATION IT COUNTS.** The number gating the page
> came from one table; every number *on* the page is drawn from
> `PledgeQuizAnswer` (per question) or from `PledgeTaskCompletion.score` (the
> score band). Three populations, and the gate was on the one that is easiest
> to inflate and hardest to look at. Before trusting a minimum, ask what it
> counts and what the page prints — and check they are the same set.

This is a near relative of the shape CLAUDE.md already tracks under v3.20.0
("N pledges marked" counted rows rather than marks) and under v3.16.2 ("when
redacting, ask what the redacted view can be joined against"). It is not quite
either: nothing here is joined, and nothing is miscounted for display. The
protection was simply attached to a different set from the one it protects.

⚠️ EDUCATORS ARE UNAFFECTED, deliberately. A chair reviewing a quiz is entitled
to every number, including one submission's. All three gates below are inside
`elif viewer_is_pledge`, and the controls at the bottom pin that — a suppression
that silently spread to the grading workflow would be a regression, not a fix.
"""
from django.test import TestCase

from src.models import (ParliamentUser, PledgeQuizAnswer, PledgeTask,
                        PledgeTaskCompletion, PledgeTaskQuestion)
from src.view.committee.education import (PLEDGE_ANALYSIS_MIN_SUBMISSIONS,
                                          quiz_analysis_context)


def _pledge(uid):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'Pledge {uid}', username=uid,
        member_type='Pledge', member_status='Active',
    )
    user.set_password('quiz-population-test-pass-12345!')
    user.save()
    return user


def _quiz(**kwargs):
    return PledgeTask.objects.create(
        title='Quiz', task_type='quiz', activation_mode='immediate',
        show_analysis_to_pledges=True, max_score=10, **kwargs
    )


def _sat(task, question_map, pledge, score=None):
    """One pledge sits the quiz: a completion plus real answers."""
    completion = PledgeTaskCompletion.objects.create(
        task=task, pledge=pledge, status='completed', score=score,
    )
    for question, correct in question_map.items():
        PledgeQuizAnswer.objects.create(
            pledge=pledge, question=question, answer_text='x', is_correct=correct,
        )
    return completion


class TheGateCountsAnswerersNotRowsTests(TestCase):

    def test_chair_marked_rows_do_not_unlock_the_breakdown(self):
        """The reproduction."""
        sitter, other_a, other_b = _pledge('P-SAT'), _pledge('P-OA'), _pledge('P-OB')
        task = _quiz()
        q1 = PledgeTaskQuestion.objects.create(task=task, question_text='Q1', display_order=1)
        q2 = PledgeTaskQuestion.objects.create(task=task, question_text='Q2', display_order=2)

        _sat(task, {q1: True, q2: False}, sitter, score=4)
        # The chair marks two pledges who never sat it. `get_or_create`, no answers.
        PledgeTaskCompletion.objects.create(task=task, pledge=other_a, status='waived')
        PledgeTaskCompletion.objects.create(task=task, pledge=other_b, status='incomplete')

        context = quiz_analysis_context(task, viewer_is_pledge=True)

        self.assertEqual(
            context['submissions'], 1,
            'Three completion rows, one person answered. The page is about the '
            'people who answered.',
        )
        self.assertTrue(
            context['withheld'],
            'One answerer behind three completion rows still identifies him.',
        )
        self.assertEqual(context['rows'], [])
        self.assertIsNone(context['score_low'])
        self.assertIsNone(context['score_high'])

    def test_a_question_nobody_answered_does_not_ride_on_the_others(self):
        """
        Per question, because questions are NOT answered the same number of
        times. v3.20.0 records that a chair may add a question to a quiz people
        have already sat; that question then carries only the answers given
        since, and a page-level count says nothing about it.
        """
        task = _quiz()
        old = PledgeTaskQuestion.objects.create(task=task, question_text='Old', display_order=1)
        new = PledgeTaskQuestion.objects.create(task=task, question_text='New', display_order=2)

        for n in range(PLEDGE_ANALYSIS_MIN_SUBMISSIONS):
            _sat(task, {old: n != 0}, _pledge(f'P-OLD{n}'))
        # One further pledge sat it after the new question was added.
        _sat(task, {old: True, new: False}, _pledge('P-NEW'))

        context = quiz_analysis_context(task, viewer_is_pledge=True)
        by_text = {r['question'].question_text: r for r in context['rows']}

        self.assertFalse(context['withheld'])
        self.assertFalse(by_text['Old']['suppressed'])
        self.assertIsNotNone(by_text['Old']['percent'])

        self.assertTrue(
            by_text['New']['suppressed'],
            'One answer to this question, and the page would have printed it as '
            '0% or 100% — a single pledge\'s mark rendered as a class statistic.',
        )
        self.assertIsNone(by_text['New']['percent'])
        self.assertIsNone(by_text['New']['correct'])
        self.assertIsNone(by_text['New']['wrong'])

    def test_a_lone_score_is_not_printed_as_a_band(self):
        """
        `score_low` and `score_high` with one scored completion are one pledge's
        mark under two labels, and `score_average` is a third.
        """
        task = _quiz()
        q1 = PledgeTaskQuestion.objects.create(task=task, question_text='Q1', display_order=1)

        _sat(task, {q1: True}, _pledge('P-S1'), score=9)
        _sat(task, {q1: False}, _pledge('P-S2'))    # sat it, not yet graded
        _sat(task, {q1: True}, _pledge('P-S3'))     # sat it, not yet graded

        context = quiz_analysis_context(task, viewer_is_pledge=True)

        self.assertFalse(context['withheld'], 'Three answerers — the page opens.')
        self.assertEqual(context['score_count'], 0)
        self.assertIsNone(context['score_low'])
        self.assertIsNone(context['score_high'])
        self.assertIsNone(context['score_average'])


class TheEducatorViewIsUnchangedTests(TestCase):
    """
    CONTROLS. Every gate above is conditioned on `viewer_is_pledge`, and a
    suppression that leaked into the grading workflow would be a regression
    dressed as a fix — the chair marking the quiz is entitled to the answers.
    """

    def setUp(self):
        self.task = _quiz()
        self.q1 = PledgeTaskQuestion.objects.create(
            task=self.task, question_text='Q1', display_order=1,
        )
        _sat(self.task, {self.q1: True}, _pledge('P-ONLY'), score=4)

    def test_one_submission_is_fully_visible_to_an_educator(self):
        context = quiz_analysis_context(
            self.task, is_chair=True, viewer_is_pledge=False,
        )

        self.assertFalse(context['withheld'])
        self.assertEqual(context['submissions'], 1)
        self.assertEqual(context['rows'][0]['percent'], 100)
        self.assertFalse(context['rows'][0]['suppressed'])
        self.assertEqual(context['score_low'], 4)
        self.assertEqual(context['score_high'], 4)

    def test_the_suppressed_key_is_always_present(self):
        """
        Defaulted in the row builder, not only where it is set — a template
        resolving a missing key to `''` is how v3.19.x's feature flags failed
        closed and invisibly.
        """
        for viewer_is_pledge in (True, False):
            with self.subTest(viewer_is_pledge=viewer_is_pledge):
                context = quiz_analysis_context(
                    self.task, viewer_is_pledge=viewer_is_pledge,
                )
                for row in context['rows']:
                    self.assertIn('suppressed', row)
