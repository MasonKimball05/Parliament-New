"""
A pledge sees class totals only when there is a class (v3.21.5).

⚠️ WHAT THIS IS ABOUT. v3.21.0 gave pledges a question-by-question breakdown,
gated on the chair ticking `show_analysis_to_pledges`. The gate is the right
one and it answers the wrong question: it decides *whether* the class may see
class totals, not whether what is on the page is a class total at all.

Reproduced on 08-20-26 against v3.21.4: one pledge submits a scored quiz, a
second pledge — who has submitted nothing — opens the page and reads

    Submissions 1 · Average 4 · Lowest 4 · Highest 4
    "Who founded Beta?"  1 answer · 0 right · 1 wrong

directly beneath the sentence *"These are class totals. Nobody's individual
answers are shown here."* It is the first submitter who is exposed, and being
first is not unusual — it is the state every quiz passes through.

The threshold matches `announcement_polls`' `respondent_count > 2`, which this
codebase added for the same reason and wrote down as *"prevents identifying
early respondents by elimination"*.

⚠️ EVERY TEST HERE FAILS AGAINST THE PRE-FIX TREE except the two controls, which
are the point: an educator must keep seeing everything, or the fix has traded a
disclosure for a broken feature.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import (
    PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion, PledgeQuizAnswer,
)
from src.test_education_scoring_and_meetings import EducationFixtureMixin, make_user
from src.view.committee.education import PLEDGE_ANALYSIS_MIN_SUBMISSIONS


class QuizAnalysisThresholdTests(EducationFixtureMixin, TestCase):
    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(
            title='Founders Quiz', task_type='quiz', max_score=10,
            show_analysis_to_pledges=True, is_active=True,
        )
        self.question = PledgeTaskQuestion.objects.create(
            task=self.task, question_text='Who founded Beta?', display_order=1,
        )

    def _submit(self, pledge, score, correct):
        PledgeTaskCompletion.objects.create(task=self.task, pledge=pledge, score=score)
        PledgeQuizAnswer.objects.create(
            question=self.question, pledge=pledge, answer_text='...', is_correct=correct,
        )

    def _pledge_page(self, viewer):
        self.client.force_login(viewer)
        return self.client.get(reverse('pledge_quiz_analysis', args=[self.task.pk]))

    # -- the disclosure ----------------------------------------------------

    def test_a_single_submission_is_not_shown_to_another_pledge(self):
        """The exact reproduction: one submitter, one reader, nothing to read."""
        self._submit(self.pledge, 4, False)

        response = self._pledge_page(self.other_pledge)
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['withheld'])
        self.assertIsNone(response.context['score_low'])
        self.assertIsNone(response.context['score_high'])
        self.assertIsNone(response.context['score_average'])
        self.assertEqual(response.context['rows'], [])
        # And nothing leaks through the rendering either — the assertion this
        # module would be worthless without, because a context key can be
        # cleared while a template still prints the value some other way.
        self.assertNotIn('Lowest', body)
        self.assertNotIn('1 answer', body)

    def test_two_submissions_are_still_withheld(self):
        """
        Two is not a class. With two, the reader who is one of them learns the
        other's score by subtraction from the average — which is precisely the
        elimination attack the poll threshold was written against.
        """
        self._submit(self.pledge, 4, False)
        self._submit(self.other_pledge, 9, True)

        third = make_user('P-ZZ1111', 'Pledge Three', member_type='Pledge')
        response = self._pledge_page(third)

        self.assertTrue(response.context['withheld'])
        self.assertEqual(response.context['rows'], [])

    def test_the_threshold_releases_at_three(self):
        """The other side of the threshold — a threshold needs both answers."""
        self._submit(self.pledge, 4, False)
        self._submit(self.other_pledge, 9, True)
        third = make_user('P-ZZ2222', 'Pledge Three', member_type='Pledge')
        self._submit(third, 7, True)

        fourth = make_user('P-ZZ3333', 'Pledge Four', member_type='Pledge')
        response = self._pledge_page(fourth)

        self.assertFalse(response.context['withheld'])
        self.assertEqual(response.context['score_low'], 4)
        self.assertEqual(response.context['score_high'], 9)
        self.assertEqual(len(response.context['rows']), 1)

    def test_the_constant_is_the_one_the_view_uses(self):
        """
        Guards against the threshold being tuned in one place and asserted in
        another — the shape of v3.19.6's budget bug, where the code compared one
        thing and three comments described another.
        """
        self._submit(self.pledge, 4, False)
        for index in range(PLEDGE_ANALYSIS_MIN_SUBMISSIONS - 2):
            self._submit(
                make_user(f'P-TH{index:04d}', f'Pledge {index}', member_type='Pledge'),
                5, True,
            )
        reader = make_user('P-READER', 'Reader', member_type='Pledge')
        self.assertTrue(self._pledge_page(reader).context['withheld'])

        self._submit(make_user('P-LAST01', 'Last', member_type='Pledge'), 5, True)
        self.assertFalse(self._pledge_page(reader).context['withheld'])

    def test_zero_and_too_few_read_differently(self):
        """
        "Nobody has taken this" and "not enough people have taken this" are
        opposite messages. The page has to be able to tell them apart, which is
        why `withheld` is a flag rather than the rows simply being empty.
        """
        empty = self._pledge_page(self.other_pledge).content.decode()
        self.assertIn('Nobody has taken this quiz yet', empty)

        self._submit(self.pledge, 4, False)
        few = self._pledge_page(self.other_pledge).content.decode()
        self.assertNotIn('Nobody has taken this quiz yet', few)
        self.assertIn('Results appear once', few)

    # -- the controls ------------------------------------------------------

    def test_the_educator_still_sees_a_single_submission(self):
        """
        CONTROL. The chair is entitled to individual results and reaches them
        from the grading page regardless, so withholding the summary from him
        would remove information and protect nobody.
        """
        self._submit(self.pledge, 4, False)

        self.client.force_login(self.chair)
        response = self.client.get(
            reverse('education_quiz_analysis', args=[self.committee.code, self.task.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['withheld'])
        self.assertEqual(response.context['score_low'], 4)
        self.assertEqual(len(response.context['rows']), 1)

    def test_the_pledge_gate_itself_still_applies(self):
        """
        CONTROL. The threshold is a second gate, not a replacement: a quiz whose
        chair has not opted in is still a 404 no matter how many have submitted.
        """
        self.task.show_analysis_to_pledges = False
        self.task.save(update_fields=['show_analysis_to_pledges'])
        for index in range(4):
            self._submit(
                make_user(f'P-GA{index:04d}', f'Pledge {index}', member_type='Pledge'),
                5, True,
            )

        self.assertEqual(self._pledge_page(self.other_pledge).status_code, 404)
