"""
v3.48.0 — quiz points: full for completing, or by % right.

Mason, 10-16-26: "If a quiz is 10 points and they get 7 right it's 7 points
they get not the full 10." Source of "% right" (Mason: "a bit of both"): the
chair's typed score if there is one, else the answers marked right. Points
only count once the quiz is marked Completed, in both modes.
"""
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from src.models import PledgeQuizAnswer, PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion
from src.pledge_points import task_points_earned
from src.tests.education._fixtures import EducationFixtureMixin


class _T:  # a stand-in task/completion for the pure math
    def __init__(self, **kw):
        self.__dict__.update(kw)


def task(points=10, mode='by_score', kind='quiz', max_score=None):
    return _T(points=points, points_mode=mode, task_type=kind, max_score=max_score)


def done(score=None, status='completed'):
    return _T(score=score, status=status)


class PointsMathTests(SimpleTestCase):

    def test_seven_of_ten_right_is_seven_points(self):
        self.assertEqual(task_points_earned(task(), done(), correct=7, question_count=10), 7)

    def test_full_mode_ignores_how_many_were_right(self):
        self.assertEqual(task_points_earned(task(mode='full'), done(), 2, 10), 10)

    def test_non_quiz_is_always_full_even_if_set_to_by_score(self):
        self.assertEqual(task_points_earned(task(kind='task'), done(), 2, 10), 10)

    def test_typed_score_wins_over_marks(self):
        t = task(points=10, max_score=20)
        self.assertEqual(task_points_earned(t, done(score=15), correct=2, question_count=10), 8)  # 7.5 -> 8

    def test_marks_used_when_no_score_typed(self):
        t = task(points=10, max_score=20)
        self.assertEqual(task_points_earned(t, done(score=None), correct=3, question_count=4), 8)  # 7.5 -> 8

    def test_rounds_half_up_and_never_exceeds_points(self):
        self.assertEqual(task_points_earned(task(points=5), done(), 1, 2), 3)   # 2.5 -> 3
        self.assertEqual(task_points_earned(task(points=5), done(), 1, 3), 2)   # 1.67 -> 2
        self.assertEqual(task_points_earned(task(points=10, max_score=5), done(score=9), 0, 0), 10)

    def test_nothing_to_scale_by_gives_full_points(self):
        self.assertEqual(task_points_earned(task(), done(), 0, 0), 10)

    def test_only_completed_earns_anything(self):
        for status in ('pending', 'incomplete', 'waived'):
            self.assertEqual(task_points_earned(task(), done(status=status), 10, 10), 0)
            self.assertEqual(task_points_earned(task(mode='full'), done(status=status), 10, 10), 0)
        self.assertEqual(task_points_earned(task(), None, 10, 10), 0)

    def test_zero_right_is_zero(self):
        self.assertEqual(task_points_earned(task(), done(), 0, 10), 0)


class QuizPointsOnEveryPageTests(EducationFixtureMixin, TestCase):
    """The dashboard, the pledge page and My Tasks must all say 7."""

    def setUp(self):
        cache.clear()
        self.build()
        self.quiz = PledgeTask.objects.create(
            title='Creed quiz', task_type='quiz', points=10, points_mode='by_score',
            activation_mode='immediate')
        questions = [PledgeTaskQuestion.objects.create(task=self.quiz, question_text=f'Q{i}', display_order=i)
                     for i in range(10)]
        for i, q in enumerate(questions):
            PledgeQuizAnswer.objects.create(question=q, pledge=self.pledge, answer_text='a', is_correct=i < 7)
        PledgeTaskCompletion.objects.create(task=self.quiz, pledge=self.pledge, status='completed')

    def tearDown(self):
        cache.clear()

    def test_dashboard(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        ps = {p['pledge'].pk: p for p in response.context['pledge_summaries']}[self.pledge.pk]
        self.assertEqual((ps['points'], ps['max_points']), (7, 10))

    def test_pledge_detail(self):
        response = self.client.get(reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk]))
        self.assertEqual(response.context['task_points'], 7)
        self.assertIn('7 of 10 pts', response.content.decode())

    def test_my_tasks(self):
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('my_pledge_tasks'))
        self.assertEqual(response.context['task_points'], 7)
        self.assertIn('7 of 10 pts', response.content.decode())

    def test_switching_to_full_gives_ten(self):
        PledgeTask.objects.filter(pk=self.quiz.pk).update(points_mode='full')
        self.client.force_login(self.pledge)
        self.assertEqual(self.client.get(reverse('my_pledge_tasks')).context['task_points'], 10)

    def test_marked_incomplete_earns_nothing(self):
        PledgeTaskCompletion.objects.filter(task=self.quiz).update(status='incomplete')
        response = self.client.get(reverse('education_pledge_detail', args=[self.committee.code, self.pledge.pk]))
        self.assertEqual(response.context['task_points'], 0)


class TaskFormSavesTheModeTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build()

    def post(self, **extra):
        data = {'title': 'T', 'task_type': 'quiz', 'phase': 'all', 'points': '10',
                'activation_mode': 'immediate', 'points_mode': 'by_score'}
        data.update(extra)
        return self.client.post(reverse('education_add_task', args=[self.committee.code]), data)

    def test_quiz_keeps_by_score(self):
        self.post()
        self.assertEqual(PledgeTask.objects.get(title='T').points_mode, 'by_score')

    def test_non_quiz_is_reset_to_full(self):
        self.post(task_type='task')
        self.assertEqual(PledgeTask.objects.get(title='T').points_mode, 'full')

    def test_junk_value_is_full(self):
        self.post(points_mode='everything')
        self.assertEqual(PledgeTask.objects.get(title='T').points_mode, 'full')
