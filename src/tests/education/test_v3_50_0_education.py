"""
v3.50.0 — (1) pledge page restrictions are enforced on every page, not just
the three that carried @pledge_page_allowed; (2) a "points by % right" quiz
warns when it's marked passed with answers still unmarked.
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from src.middleware.pledge_pages import is_always_allowed, restrictable_url_names
from src.models import (PledgePageRestriction, PledgeQuizAnswer, PledgeTask, PledgeTaskCompletion,
                        PledgeTaskQuestion)
from src.tests.education._fixtures import EducationFixtureMixin


class RestrictionsApplyEverywhereTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build()

    def tearDown(self):
        cache.clear()

    def as_pledge(self, url_name, phase='1'):
        type(self.pledge).objects.filter(pk=self.pledge.pk).update(pledge_phase=phase)
        self.client.force_login(self.pledge)
        return self.client.get(reverse(url_name))

    def test_a_page_without_the_decorator_is_now_blocked(self):
        # The songbook is the dashboard's own example and never had the decorator.
        self.assertEqual(self.as_pledge('songbook').status_code, 200)
        PledgePageRestriction.objects.create(url_name='songbook', allowed_phases=['2', '3'])
        cache.clear()
        self.assertEqual(self.as_pledge('songbook', phase='1').status_code, 403)
        self.assertEqual(self.as_pledge('songbook', phase='2').status_code, 200)

    def test_brothers_are_never_blocked(self):
        PledgePageRestriction.objects.create(url_name='songbook', allowed_phases=[])
        self.client.force_login(self.brother)
        self.assertEqual(self.client.get(reverse('songbook')).status_code, 200)

    def test_sign_in_pages_cannot_be_restricted(self):
        for name in ('logout', 'home', 'change_password', 'two_factor_setup'):
            self.assertTrue(is_always_allowed(name), name)
            self.assertNotIn(name, restrictable_url_names())
        r = self.client.post(reverse('education_update_page_restriction', args=[self.committee.code]),
                             {'url_name': 'home', 'display_name': 'Home'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PledgePageRestriction.objects.filter(url_name='home').exists())

    def test_a_typo_is_refused(self):
        r = self.client.post(reverse('education_update_page_restriction', args=[self.committee.code]),
                             {'url_name': 'songbok', 'display_name': 'Songbook'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('no page called', r.json()['error'])
        r = self.client.post(reverse('education_update_page_restriction', args=[self.committee.code]),
                             {'url_name': 'songbook', 'display_name': 'Songbook', 'phases': ['2']})
        self.assertEqual(r.status_code, 200)

    def test_the_dashboard_offers_the_page_list(self):
        html = self.client.get(reverse('education_home', args=[self.committee.code])).content.decode()
        self.assertIn('<datalist id="restrictable-pages">', html)
        self.assertIn('<option value="songbook">', html)


class ByScoreGradingWarningTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build()
        self.quiz = PledgeTask.objects.create(title='Creed quiz', task_type='quiz', points=10,
                                              points_mode='by_score', activation_mode='immediate')
        self.answers = []
        for i in range(10):
            q = PledgeTaskQuestion.objects.create(task=self.quiz, question_text=f'Q{i}', display_order=i)
            # first 4 marked right, 3 marked wrong, last 3 unmarked
            mark = True if i < 4 else (False if i < 7 else None)
            self.answers.append(PledgeQuizAnswer.objects.create(question=q, pledge=self.pledge,
                                                                answer_text='a', is_correct=mark))
        self.url = reverse('education_toggle_completion', args=[self.committee.code, self.quiz.pk, self.pledge.pk])

    def tearDown(self):
        cache.clear()

    def test_grading_page_explains_and_previews(self):
        html = self.client.get(reverse('education_quiz_submissions',
                                       args=[self.committee.code, self.quiz.pk])).content.decode()
        self.assertIn('This quiz gives points by % right', html)
        self.assertIn('4 of 10 pts · 3 not marked', html)

    def test_marking_passed_with_unmarked_answers_warns(self):
        r = self.client.post(self.url, {'set_status': 'completed'}, HTTP_REFERER='http://testserver/x/', follow=True)
        msgs = [str(m) for m in r.context['messages']]
        self.assertTrue(any('3 of 10 answers' in m and '4 of 10 pts' in m for m in msgs), msgs)
        self.assertEqual(PledgeTaskCompletion.objects.get(task=self.quiz).status, 'completed')  # not blocked

    def test_dashboard_grid_gets_the_warning_too(self):
        PledgeTaskCompletion.objects.create(task=self.quiz, pledge=self.pledge, status='pending')
        r = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertIn('3 of 10 answers', r.json()['warning'])

    def test_no_warning_once_everything_is_marked(self):
        PledgeQuizAnswer.objects.filter(question__task=self.quiz, is_correct=None).update(is_correct=True)
        r = self.client.post(self.url, {'set_status': 'completed'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json()['warning'], '')

    def test_full_points_quiz_never_warns(self):
        PledgeTask.objects.filter(pk=self.quiz.pk).update(points_mode='full')
        r = self.client.post(self.url, {'set_status': 'completed'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json()['warning'], '')
