"""
Mobile plan, Phase 3f — the Education dashboard.

Access is _get_education_access (chairs and admins, or a permission row);
grading needs can_grade_submissions, attendance / absences / phases need
can_manage_tasks; the numbers are education_overview's and pledge_progress's.

    python manage.py test src.tests.mobile.test_education
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    Committee, EducationAbsenceRequest, EducationMeeting, EducationMeetingAttendance, EducationMemberPermission, Event,
    PledgeQuizAnswer, PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/committees/EDU/education/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class EducationTests(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('ed-chair')
        self.member = make_user('ed-mem')
        _, self.chair_access, _ = make_device(self.chair)
        _, self.member_access, _ = make_device(self.member)
        self.pledge = make_user('ed-p1', member_type='Pledge', is_active=True)
        self.c = Committee.objects.create(name='Education', code='EDU', is_education_committee=True, is_active=True)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.member)
        self.task = PledgeTask.objects.create(title='Learn the creed', points=10)
        self.api = Client()

    def get(self, path='', access=None):
        return self.api.get(API + path, **bearer(access or self.chair_access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, json.dumps(body or {}), content_type='application/json',
                             **bearer(access or self.chair_access))

    def meeting(self, days):
        e = Event.objects.create(title='Pledge meeting', description='d', created_by=self.chair,
                                 date_time=timezone.now() + timedelta(days=days))
        return EducationMeeting.objects.create(event=e, committee=self.c, points=5)

    def test_member_without_permission_is_refused(self):
        self.assertEqual(self.get(access=self.member_access).status_code, 404)
        EducationMemberPermission.objects.create(committee=self.c, user=self.member, can_view_submissions=True)
        body = self.get(access=self.member_access).json()
        self.assertFalse(body['access']['can_manage_tasks'])
        self.assertEqual(body['web_links'], [])

    def test_dashboard_numbers(self):
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='completed')
        body = self.get().json()
        p = body['pledges'][0]
        self.assertEqual((p['user_id'], p['completed'], p['total'], p['points'], p['max_points']),
                         ('ed-p1', 1, 1, 10, 10))
        self.assertEqual(body['tasks'][0]['done_count'], 1)

    def test_pledge_detail(self):
        body = self.get('pledges/ed-p1/').json()
        self.assertEqual([t['title'] for t in body['tasks']], ['Learn the creed'])
        self.assertIsNone(body['tasks'][0]['completion'])

    def test_grading(self):
        r = self.post(f'tasks/{self.task.id}/pledges/ed-p1/', {'status': 'completed'}, self.member_access)
        self.assertEqual(r.status_code, 404)   # no permission row: no education access at all
        EducationMemberPermission.objects.create(committee=self.c, user=self.member, can_view_submissions=True)
        r = self.post(f'tasks/{self.task.id}/pledges/ed-p1/', {'status': 'completed'}, self.member_access)
        self.assertEqual(r.json()['error'], 'forbidden')
        r = self.post(f'tasks/{self.task.id}/pledges/ed-p1/', {'status': 'completed', 'score': 3})
        self.assertEqual(r.json()['error'], 'invalid_score')    # the task has no max score
        r = self.post(f'tasks/{self.task.id}/pledges/ed-p1/', {'status': 'completed'})
        self.assertEqual(r.json()['completion']['status'], 'completed')

    def test_quiz_answers(self):
        quiz = PledgeTask.objects.create(title='Quiz', task_type='quiz', points=4, points_mode='by_score')
        q = PledgeTaskQuestion.objects.create(task=quiz, question_text='Founded?')
        a = PledgeQuizAnswer.objects.create(question=q, pledge=self.pledge, answer_text='1839')
        body = self.get(f'tasks/{quiz.id}/').json()
        self.assertEqual(body['pledges'][0]['answers'][0]['text'], '1839')
        self.assertEqual(self.post(f'answers/{a.id}/', {'verdict': 'correct'}).json(), {'is_correct': True})
        r = self.post(f'tasks/{quiz.id}/pledges/ed-p1/', {'status': 'completed'}).json()
        self.assertEqual(r['warning'], '')

    def test_attendance_and_absence(self):
        m = self.meeting(-1)
        other = make_user('ed-brother')
        r = self.post(f'meetings/{m.id}/attendance/', {'statuses': {'ed-p1': 'present', 'ed-brother': 'present'}})
        self.assertEqual(r.json()['pledges'][0]['status'], 'present')
        self.assertFalse(EducationMeetingAttendance.objects.filter(pledge=other).exists())
        future = self.meeting(3)
        absence = EducationAbsenceRequest.objects.create(meeting=future, pledge=self.pledge, reason='Exam')
        self.assertEqual(self.post(f'absences/{absence.id}/', {'decision': 'approved'}).json(), {'status': 'approved'})
        self.assertEqual(EducationMeetingAttendance.objects.get(meeting=future).status, 'excused')

    def test_phases(self):
        self.assertEqual(self.post('pledges/ed-p1/phase/', {'phase': '4'}).json()['error'], 'invalid_choice')
        self.post('pledges/ed-p1/phase/', {'phase': '2'})
        self.assertEqual(self.post('phases/advance/').json(), {'moved': 1})
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.pledge_phase, '3')
