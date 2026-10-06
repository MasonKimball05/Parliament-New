"""
v3.44.6 — quiz questions are numbered from 1, by position.

Mason, 10-06-26: "the education quizzes start at 0 not 1."

`PledgeTaskQuestion.display_order` is a sort key. It defaults to 0, and the
"Add a question" form used to suggest `number of questions so far`, so a
quiz's questions were stored as 0, 1, 2. The manage-questions page printed
that number as the label ("Q0."). Every other quiz page (the pledge's quiz,
submissions, grading) already numbers by position, so the same question was
"Q0" to the chair and "1." to the pledge.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import PledgeTask, PledgeTaskQuestion
from src.tests.education._fixtures import EducationFixtureMixin


class ManagePageNumbersFromOneTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        self.build()
        self.quiz = PledgeTask.objects.create(title='Founders Quiz', task_type='quiz')
        self.url = reverse('education_manage_quiz_questions', args=[self.committee.code, self.quiz.pk])

    def _html(self):
        return self.client.get(self.url).content.decode()

    def test_questions_stored_from_zero_are_labelled_from_one(self):
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='First question', display_order=0)
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Second question', display_order=1)
        html = self._html()
        self.assertIn('Q1. First question', html)
        self.assertIn('Q2. Second question', html)
        self.assertNotIn('Q0.', html)

    def test_gaps_in_the_sort_key_do_not_show_in_the_numbering(self):
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='First question', display_order=5)
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Second question', display_order=20)
        html = self._html()
        self.assertIn('Q1. First question', html)
        self.assertIn('Q2. Second question', html)

    def test_the_add_form_suggests_one_for_the_first_question(self):
        self.assertIn('name="display_order" value="1"', self._html())

    def test_the_add_form_suggests_a_value_after_the_existing_questions(self):
        """Existing quizzes are stored 0..n-1; n+1 still sorts last."""
        for order in (0, 1, 2):
            PledgeTaskQuestion.objects.create(task=self.quiz, question_text=f'Stored {order}', display_order=order)
        self.assertIn('name="display_order" value="4"', self._html())


class MissingAnswerMessageTests(EducationFixtureMixin, TestCase):
    """The pledge's page numbers questions 1, 2, 3 by position. The "please
    answer question N" message must use the same N whatever the sort keys are."""

    def setUp(self):
        self.build()
        self.task = PledgeTask.objects.create(title='Quiz', task_type='quiz')
        self.pledge_client = Client()
        self.pledge_client.force_login(self.pledge)
        self.url = reverse('pledge_take_quiz', args=[self.task.pk])

    def _post_only_first(self, orders):
        first, second = (
            PledgeTaskQuestion.objects.create(task=self.task, question_text=f'Question {i}', display_order=o)
            for i, o in enumerate(orders)
        )
        response = self.pledge_client.post(self.url, {f'answer_{first.pk}': 'an answer'})
        return response.content.decode()

    def test_keys_from_zero(self):
        self.assertIn('Please answer question 2.', self._post_only_first((0, 1)))

    def test_keys_from_one(self):
        html = self._post_only_first((1, 2))
        self.assertIn('Please answer question 2.', html)
        self.assertNotIn('Please answer question 3.', html)

    def test_keys_all_the_same(self):
        """Two questions left at the default 0 used to both be "question 1"."""
        self.assertIn('Please answer question 2.', self._post_only_first((0, 0)))
