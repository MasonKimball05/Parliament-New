"""
The education committee's officer tools in the app (11-02-26,
src/mobile/views_education_manage.py): tasks, quiz questions, the quiz
breakdown, "view as pledge", point adjustments, meetings, bigs, pledge page
access and member permissions.

Each endpoint calls the same helper as its web view, so these check who may
use it (the web's permission), that it shows what the web page shows, that bad
input is refused with ``invalid_input``, and that a write does what the web's
does (drafts, cascades, the pledge's profile big, the restriction cache).

    python manage.py test src.tests.mobile.test_education_batch6
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Committee, EducationMeeting, EducationMeetingAttendance, EducationMemberPermission, Event, PledgeBigAssignment,
    PledgePageRestriction, PledgePointAdjustment, PledgeQuizAnswer, PledgeTask, PledgeTaskCompletion,
    PledgeTaskQuestion, Song,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view.committee.education import POINT_ADJUSTMENT_LIMIT

BASE = '/api/mobile/v1/committees/EDU/'
API = BASE + 'education/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.chair = make_user('ed-chair', member_type='Officer')
        self.viewer = make_user('ed-view')        # view submissions only
        self.manager = make_user('ed-mgr')        # manage tasks (no chair)
        self.outsider = make_user('ed-out')       # no education access
        self.brother = make_user('ed-bro')
        self.pledge = make_user('P-C7JKZY', member_type='Pledge', is_active=True, pledge_phase='1')
        self.pledge2 = make_user('P-9QW2LM', member_type='Pledge', is_active=True, pledge_phase='2')
        self.c = Committee.objects.create(name='Education', code='EDU', is_education_committee=True, is_active=True)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.viewer, self.manager, self.outsider)
        EducationMemberPermission.objects.create(committee=self.c, user=self.viewer, can_view_submissions=True)
        EducationMemberPermission.objects.create(committee=self.c, user=self.manager, can_manage_tasks=True)
        self.tokens = {u.user_id: make_device(u)[1] for u in (self.chair, self.viewer, self.manager, self.outsider,
                                                               self.pledge)}
        self.api = Client()
        self.web = Client()
        self.web.force_login(self.chair)

    def get(self, path, who=None, base=API):
        return self.api.get(base + path, **bearer(self.tokens[(who or self.chair).user_id]))

    def post(self, path, body=None, who=None, base=API):
        return self.api.post(base + path, json.dumps(body or {}), content_type='application/json',
                             **bearer(self.tokens[(who or self.chair).user_id]))

    def assertRefused(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()['error'], code)

    def meeting(self, days=3, **kw):
        e = Event.objects.create(title='Pledge meeting', description='d', created_by=self.chair,
                                 date_time=timezone.now() + timedelta(days=days))
        return EducationMeeting.objects.create(event=e, committee=self.c, points=5, **kw)


class PermissionTests(_Base):
    """Manage endpoints need can_manage_tasks; reading the breakdown and preview needs any access."""

    def test_no_education_access_is_not_found(self):
        task = PledgeTask.objects.create(title='T')
        for path in ('tasks/new/', f'tasks/{task.id}/edit/', 'bigs/', 'page-access/', 'meetings/new/',
                     f'pledges/{self.pledge.pk}/preview/'):
            with self.subTest(path=path):
                self.assertEqual(self.get(path, self.outsider).status_code, 404)

    def test_view_only_access_cannot_manage(self):
        task = PledgeTask.objects.create(title='T', task_type='quiz')
        m = self.meeting()
        for method, path in (
            (self.get, 'tasks/new/'), (self.post, 'tasks/new/'), (self.get, f'tasks/{task.id}/edit/'),
            (self.post, f'tasks/{task.id}/publish/'), (self.post, f'tasks/{task.id}/delete/'),
            (self.post, f'tasks/{task.id}/duplicate/'), (self.get, f'tasks/{task.id}/questions/'),
            (self.post, f'pledges/{self.pledge.pk}/points/'), (self.get, 'meetings/new/'),
            (self.post, f'meetings/{m.id}/delete/'), (self.get, 'bigs/'), (self.post, 'bigs/reveal-all/'),
            (self.get, 'page-access/'),
        ):
            with self.subTest(path=path, method=method.__name__):
                self.assertRefused(method(path, who=self.viewer), 403, 'forbidden')
        self.assertTrue(PledgeTask.objects.get(pk=task.pk).is_active)
        self.assertTrue(EducationMeeting.objects.filter(pk=m.pk).exists())

    def test_view_only_access_reads_the_breakdown_and_the_preview(self):
        quiz = PledgeTask.objects.create(title='Quiz', task_type='quiz')
        self.assertEqual(self.get(f'tasks/{quiz.id}/analysis/', self.viewer).status_code, 200)
        self.assertEqual(self.get(f'pledges/{self.pledge.pk}/preview/', self.viewer).status_code, 200)

    def test_manage_permission_without_being_chair_is_enough(self):
        self.assertEqual(self.get('tasks/new/', self.manager).status_code, 200)

    def test_a_page_toggle_off_closes_them(self):
        from src.models_feature_flags import PageToggle
        PageToggle.objects.update_or_create(url_name='committee_home', defaults={'is_enabled': False})
        cache.clear()
        self.assertRefused(self.get('tasks/new/'), 403, 'page_disabled')


class TaskTests(_Base):

    def task_body(self, **kw):
        return {'title': 'Learn the creed', 'description': 'All of it', 'task_type': 'task', 'phase': '1',
                'due_date': '2026-11-20', 'is_required': True, 'points': 10, 'max_score': None,
                'points_mode': 'full', 'show_analysis_to_pledges': False, 'display_order': 3,
                'activation_mode': 'immediate', 'activates_at': '', 'song': None, 'assigned_to': [], **kw}

    def test_create_matches_the_web_form(self):
        r = self.post('tasks/new/', self.task_body(assigned_to=[self.pledge.pk]))
        self.assertEqual(r.status_code, 201, r.content)
        task = PledgeTask.objects.get(pk=r.json()['task']['id'])
        self.web.post(reverse('education_add_task', args=['EDU']), {
            'title': 'Learn the creed', 'description': 'All of it', 'task_type': 'task', 'phase': '1',
            'due_date': '2026-11-20', 'is_required': 'on', 'points': '10', 'max_score': '',
            'points_mode': 'full', 'display_order': '3', 'activation_mode': 'immediate',
            'assigned_to': [self.pledge.pk]})
        web_task = PledgeTask.objects.exclude(pk=task.pk).get(title='Learn the creed')
        fields = ('description', 'task_type', 'phase', 'due_date', 'is_required', 'points', 'max_score',
                  'points_mode', 'show_analysis_to_pledges', 'display_order', 'activation_mode', 'is_published')
        self.assertEqual([getattr(task, f) for f in fields], [getattr(web_task, f) for f in fields])
        self.assertEqual(list(task.assigned_to.all()), list(web_task.assigned_to.all()))
        self.assertEqual(task.created_by, self.chair)

    def test_a_manual_task_starts_as_a_draft_and_a_quiz_goes_to_its_questions(self):
        r = self.post('tasks/new/', self.task_body(activation_mode='manual', task_type='quiz'))
        self.assertFalse(PledgeTask.objects.get(pk=r.json()['task']['id']).is_published)
        self.assertTrue(r.json()['add_questions'])

    def test_validation(self):
        self.assertRefused(self.post('tasks/new/', self.task_body(title='  ')), 400, 'invalid_input')
        self.assertRefused(self.post('tasks/new/', self.task_body(title='x' * 201)), 400, 'invalid_input')
        self.assertFalse(PledgeTask.objects.exists())
        # Junk choices fall back to the defaults, as the web form's do.
        r = self.post('tasks/new/', self.task_body(task_type='nope', phase='9', points=-4))
        task = PledgeTask.objects.get(pk=r.json()['task']['id'])
        self.assertEqual((task.task_type, task.phase, task.points), ('task', 'all', 0))

    def test_the_web_form_refuses_a_title_too_long_for_its_column(self):
        r = self.web.post(reverse('education_add_task', args=['EDU']), {'title': 'x' * 201})
        self.assertEqual(r.status_code, 400)

    def test_edit_form_shows_what_the_web_edit_page_does(self):
        song = Song.objects.create(title='Old song', lyrics='la', is_active=False)
        task = PledgeTask.objects.create(title='Song', task_type='song', song=song, points=3)
        task.assigned_to.set([self.pledge])
        body = self.get(f'tasks/{task.id}/edit/').json()
        ctx = self.web.get(reverse('education_edit_task', args=['EDU', task.pk])).context
        self.assertEqual([s['id'] for s in body['options']['songs']], [s.pk for s in ctx['songs']])
        self.assertIn(song.pk, [s['id'] for s in body['options']['songs']])   # its own, though deleted
        self.assertEqual(set(body['task']['assigned_to']), set(ctx['assigned_pks']))
        self.assertEqual([p['user_id'] for p in body['options']['pledges']], [p.pk for p in ctx['all_pledges']])
        self.assertEqual(body['task']['song'], song.pk)

    def test_edit_keeps_marks_and_reassigns(self):
        task = PledgeTask.objects.create(title='T', points=1, activation_mode='manual', is_published=True)
        task.assigned_to.set([self.pledge])
        PledgeTaskCompletion.objects.create(task=task, pledge=self.pledge, status='completed')
        r = self.post(f'tasks/{task.id}/edit/', self.task_body(title='T2', activation_mode='manual', assigned_to=[]))
        self.assertEqual(r.status_code, 200, r.content)
        task.refresh_from_db()
        self.assertEqual(task.title, 'T2')
        self.assertTrue(task.is_published)              # an edit doesn't unpublish
        self.assertFalse(task.assigned_to.exists())     # unticking everyone reopens it
        self.assertTrue(PledgeTaskCompletion.objects.filter(task=task).exists())

    def test_publish_delete_duplicate(self):
        quiz = PledgeTask.objects.create(title='Quiz', task_type='quiz', activation_mode='manual',
                                         is_published=False, due_date=timezone.localdate())
        PledgeTaskQuestion.objects.create(task=quiz, question_text='Founded?', answer_hint='1839', display_order=2)
        self.assertTrue(self.post(f'tasks/{quiz.id}/publish/').json()['is_published'])
        r = self.post(f'tasks/{quiz.id}/duplicate/')
        clone = PledgeTask.objects.get(pk=r.json()['task']['id'])
        self.assertEqual((clone.title, clone.is_published, clone.activation_mode, clone.due_date),
                         ('Quiz (copy)', False, 'manual', None))
        self.assertEqual(list(clone.questions.values_list('question_text', 'answer_hint', 'display_order')),
                         [('Founded?', '1839', 2)])
        self.assertEqual(self.post(f'tasks/{quiz.id}/delete/').json(), {'deleted': True})
        quiz.refresh_from_db()
        self.assertFalse(quiz.is_active)


class QuizTests(_Base):

    def setUp(self):
        super().setUp()
        self.quiz = PledgeTask.objects.create(title='Quiz', task_type='quiz', points=4, points_mode='by_score')

    def test_questions_add_edit_delete(self):
        r = self.post(f'tasks/{self.quiz.id}/questions/', {'question_text': 'Founded?', 'answer_hint': '1839'})
        self.assertEqual(r.status_code, 201, r.content)
        qid = r.json()['question']['id']
        self.assertRefused(self.post(f'tasks/{self.quiz.id}/questions/', {'question_text': ' '}), 400,
                           'invalid_input')
        PledgeQuizAnswer.objects.create(question_id=qid, pledge=self.pledge, answer_text='1839')
        r = self.post(f'tasks/{self.quiz.id}/questions/{qid}/', {'question_text': 'Founded when?',
                                                                  'answer_hint': '1839', 'display_order': ''})
        self.assertEqual(r.json()['question']['text'], 'Founded when?')
        self.assertEqual(r.json()['questions'][0]['answer_count'], 1)   # the answer survives an edit
        r = self.post(f'tasks/{self.quiz.id}/questions/{qid}/delete/')
        self.assertEqual(r.json()['questions'], [])
        self.assertFalse(PledgeQuizAnswer.objects.exists())

    def test_questions_only_for_a_quiz(self):
        task = PledgeTask.objects.create(title='Plain')
        self.assertEqual(self.get(f'tasks/{task.id}/questions/').status_code, 404)

    def test_questions_list_matches_the_web_page(self):
        for n in (2, 1):
            PledgeTaskQuestion.objects.create(task=self.quiz, question_text=f'Q{n}', display_order=n)
        ctx = self.web.get(reverse('education_manage_quiz_questions', args=['EDU', self.quiz.pk])).context
        body = self.get(f'tasks/{self.quiz.id}/questions/').json()
        self.assertEqual([q['id'] for q in body['questions']], [q.pk for q in ctx['questions']])

    def test_analysis_matches_the_web_page(self):
        q1 = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='A')
        q2 = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='B', display_order=1)
        PledgeQuizAnswer.objects.create(question=q1, pledge=self.pledge, answer_text='x', is_correct=True)
        PledgeQuizAnswer.objects.create(question=q2, pledge=self.pledge, answer_text='y', is_correct=False)
        body = self.get(f'tasks/{self.quiz.id}/analysis/', self.viewer).json()
        ctx = self.web.get(reverse('education_quiz_analysis', args=['EDU', self.quiz.pk])).context
        self.assertEqual(body['submissions'], ctx['submissions'])
        self.assertEqual([(q['id'], q['correct'], q['wrong'], q['percent']) for q in body['questions']],
                         [(r['question'].pk, r['correct'], r['wrong'], r['percent']) for r in ctx['rows']])

    def test_submissions_carry_the_grading_preview(self):
        q = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='A')
        PledgeTaskQuestion.objects.create(task=self.quiz, question_text='B')
        PledgeQuizAnswer.objects.create(question=q, pledge=self.pledge, answer_text='x', is_correct=True)
        body = self.get(f'tasks/{self.quiz.id}/').json()
        ctx = self.web.get(reverse('education_quiz_submissions', args=['EDU', self.quiz.pk])).context
        web_rows = {r['pledge'].pk: r['points_preview'] for r in ctx['pledge_rows']}
        for row in body['pledges']:
            self.assertEqual(row['points_preview'], web_rows[row['user_id']])
        self.assertEqual(body['pledges_with_unmarked'], ctx['pledges_with_unmarked'])


class PreviewAndPointsTests(_Base):

    def test_preview_is_his_own_my_tasks(self):
        PledgeTask.objects.create(title='Phase 2 thing', phase='2')
        PledgeTask.objects.create(title='Now', phase='1')
        mine = self.get('', who=self.pledge, base='/api/mobile/v1/pledge/tasks/').json()
        preview = self.get(f'pledges/{self.pledge.pk}/preview/').json()
        self.assertTrue(preview.pop('is_preview'))
        self.assertEqual(preview.pop('pledge')['user_id'], self.pledge.pk)
        self.assertEqual(preview, mine)
        self.assertEqual(self.get(f'pledges/{self.brother.pk}/preview/').status_code, 404)

    def test_adjust_points(self):
        r = self.post(f'pledges/{self.pledge.pk}/points/', {'current_delta': 5, 'max_delta': -2, 'reason': 'Bonus'})
        self.assertEqual(r.status_code, 201, r.content)
        adj = PledgePointAdjustment.objects.get()
        self.assertEqual((adj.current_delta, adj.max_delta, adj.reason, adj.created_by, adj.committee),
                         (5, -2, 'Bonus', self.chair, self.c))
        # The dashboard total and the pledge page's history both carry it.
        dash = {p['user_id']: p for p in self.get('').json()['pledges']}
        self.assertTrue(dash[self.pledge.pk]['has_adjustments'])
        detail = self.get(f'pledges/{self.pledge.pk}/').json()
        self.assertEqual([a['id'] for a in detail['adjustments']], [adj.pk])
        self.assertIsNone(self.get(f'pledges/{self.pledge.pk}/', self.viewer).json()['adjustments'])
        self.assertEqual(self.post(f'points/{adj.pk}/delete/').json(), {'deleted': True})
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_adjust_points_validation(self):
        for body in ({'current_delta': 0, 'max_delta': 0}, {'current_delta': POINT_ADJUSTMENT_LIMIT + 1},
                     {'max_delta': -(POINT_ADJUSTMENT_LIMIT + 1)}):
            with self.subTest(body=body):
                self.assertRefused(self.post(f'pledges/{self.pledge.pk}/points/', body), 400, 'invalid_input')
        self.assertFalse(PledgePointAdjustment.objects.exists())

    def test_another_committees_adjustment_is_not_found(self):
        other = Committee.objects.create(name='Other', code='OTH', is_education_committee=True, is_active=True)
        adj = PledgePointAdjustment.objects.create(committee=other, pledge=self.pledge, current_delta=1,
                                                   created_by=self.chair)
        self.assertEqual(self.post(f'points/{adj.pk}/delete/').status_code, 404)


class MeetingTests(_Base):

    def body(self, **kw):
        return {'title': 'Pledge meeting', 'description': 'Bring notes', 'location': 'Library',
                'date_time': (timezone.now() + timedelta(days=2)).isoformat(), 'meeting_type': 'study',
                'attendance_required': True, 'points': 5, 'notes': 'n', 'homework': [], **kw}

    def test_create_makes_an_everyone_calendar_event_with_homework(self):
        hw = PledgeTask.objects.create(title='Homework')
        r = self.post('meetings/new/', self.body(homework=[hw.pk]))
        self.assertEqual(r.status_code, 201, r.content)
        m = EducationMeeting.objects.get(pk=r.json()['meeting']['id'])
        self.assertEqual((m.event.title, m.event.location, m.event.visible_to, m.meeting_type, m.points,
                          m.attendance_required, m.committee, m.created_by),
                         ('Pledge meeting', 'Library', None, 'study', 5, True, self.c, self.chair))
        self.assertEqual(list(m.homework.all()), [hw])

    def test_validation(self):
        self.assertRefused(self.post('meetings/new/', self.body(title='')), 400, 'invalid_input')
        self.assertRefused(self.post('meetings/new/', self.body(date_time='soon')), 400, 'invalid_input')
        self.assertRefused(self.post('meetings/new/', self.body(location='x' * 301)), 400, 'invalid_input')
        self.assertFalse(Event.objects.exists())

    def test_edit_keeps_attendance_and_delete_removes_the_event(self):
        m = self.meeting()
        EducationMeetingAttendance.objects.create(meeting=m, pledge=self.pledge, status='present')
        form = self.get(f'meetings/{m.id}/edit/').json()
        self.assertEqual(form['meeting']['title'], 'Pledge meeting')
        r = self.post(f'meetings/{m.id}/edit/', self.body(title='Moved', homework=[]))
        self.assertEqual(r.status_code, 200, r.content)
        m.refresh_from_db()
        m.event.refresh_from_db()
        self.assertEqual(m.event.title, 'Moved')
        self.assertTrue(EducationMeetingAttendance.objects.filter(meeting=m).exists())
        event_id = m.event_id
        self.assertEqual(self.post(f'meetings/{m.id}/delete/').json(), {'deleted': True})
        self.assertFalse(Event.objects.filter(pk=event_id).exists())
        self.assertFalse(EducationMeetingAttendance.objects.exists())

    def test_another_committees_meeting_is_not_found(self):
        other = Committee.objects.create(name='Other', code='OTH', is_education_committee=True, is_active=True)
        e = Event.objects.create(title='x', description='d', created_by=self.chair, date_time=timezone.now())
        m = EducationMeeting.objects.create(event=e, committee=other)
        self.assertEqual(self.get(f'meetings/{m.id}/edit/').status_code, 404)
        self.assertEqual(self.post(f'meetings/{m.id}/delete/').status_code, 404)


class BigTests(_Base):

    def test_board_matches_the_web_dashboard(self):
        PledgeBigAssignment.objects.create(committee=self.c, pledge=self.pledge, big=self.brother,
                                           created_by=self.chair)
        body = self.get('bigs/').json()
        ctx = self.web.get(reverse('education_home', args=['EDU'])).context
        self.assertEqual([(r['user_id'], r['assignment'] and r['assignment']['big']['user_id']) for r in body['rows']],
                         [(r['pledge'].pk, r['assignment'] and r['assignment'].big_id) for r in ctx['big_rows']])
        self.assertEqual([c['user_id'] for c in body['candidates']], [c.pk for c in ctx['big_candidates']])
        self.assertEqual(body['unrevealed_count'], ctx['unrevealed_big_count'])

    def test_set_reveal_unreveal_delete(self):
        r = self.post(f'bigs/{self.pledge.pk}/', {'big': self.brother.pk, 'reveal_mode': 'manual'})
        self.assertEqual(r.status_code, 200, r.content)
        self.pledge.refresh_from_db()
        self.assertIsNone(self.pledge.big_brother)      # a draft is not on his profile
        self.post(f'bigs/{self.pledge.pk}/reveal/')
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.big_brother, self.brother)
        r = self.post(f'bigs/{self.pledge.pk}/unreveal/')
        self.assertIn('hidden again', r.json()['message'])
        self.pledge.refresh_from_db()
        self.assertIsNone(self.pledge.big_brother)
        self.assertEqual(PledgeBigAssignment.objects.get().reveal_mode, 'manual')
        self.post(f'bigs/{self.pledge.pk}/delete/')
        self.assertFalse(PledgeBigAssignment.objects.exists())

    def test_reveal_all(self):
        for p in (self.pledge, self.pledge2):
            PledgeBigAssignment.objects.create(committee=self.c, pledge=p, big=self.brother, created_by=self.chair)
        r = self.post('bigs/reveal-all/')
        self.assertEqual((r.json()['message'], r.json()['unrevealed_count']), ('Revealed 2 bigs.', 0))
        self.pledge2.refresh_from_db()
        self.assertEqual(self.pledge2.big_brother, self.brother)

    def test_validation(self):
        self.assertRefused(self.post(f'bigs/{self.pledge.pk}/', {'big': self.pledge2.pk}), 400, 'invalid_input')
        self.assertRefused(self.post(f'bigs/{self.pledge.pk}/', {'big': self.brother.pk, 'reveal_mode': 'timed'}),
                           400, 'invalid_input')
        self.assertFalse(PledgeBigAssignment.objects.exists())
        self.assertEqual(self.post(f'bigs/{self.pledge.pk}/reveal/').status_code, 404)

    def test_timed_reveal(self):
        when = timezone.now() + timedelta(days=1)
        self.post(f'bigs/{self.pledge.pk}/', {'big': self.brother.pk, 'reveal_mode': 'timed',
                                              'reveals_at': when.isoformat()})
        a = PledgeBigAssignment.objects.get()
        self.assertEqual((a.reveal_mode, a.reveals_at), ('timed', when))


class PageAccessTests(_Base):

    def test_update_and_delete(self):
        r = self.post('page-access/', {'url_name': 'songbook', 'display_name': 'Songbook', 'phases': ['2', 'x']})
        self.assertEqual(r.status_code, 200, r.content)
        rule = PledgePageRestriction.objects.get(url_name='songbook')
        self.assertEqual((rule.allowed_phases, rule.updated_by), (['2'], self.chair))
        self.assertFalse(PledgePageRestriction.is_allowed('songbook', '1'))
        self.assertTrue(PledgePageRestriction.is_allowed('songbook', '2'))
        self.assertIn('songbook', r.json()['pages'])
        r = self.post(f'page-access/{rule.id}/delete/')
        self.assertNotIn('songbook', [x['url_name'] for x in r.json()['restrictions']])
        self.assertFalse(PledgePageRestriction.objects.filter(url_name='songbook').exists())

    def test_validation(self):
        for name in ('', 'songbok', 'mobile_login'):
            with self.subTest(name=name):
                self.assertRefused(self.post('page-access/', {'url_name': name}), 400, 'invalid_input')


class PermissionsTests(_Base):
    P = BASE + 'education-permissions/'

    def test_chairs_and_admins_only(self):
        self.assertRefused(self.get('', self.manager, base=self.P), 403, 'forbidden')
        self.assertEqual(self.get('', base=self.P).status_code, 200)
        self.assertEqual(self.get('', base='/api/mobile/v1/committees/NOPE/education-permissions/').status_code, 404)

    def test_rows_match_the_web_page(self):
        body = self.get('', base=self.P).json()
        ctx = self.web.get(reverse('manage_education_permissions', args=['EDU'])).context
        self.assertEqual([(r['user_id'], r['is_chair'], r['flags']) for r in body['rows']],
                         [(r['member'].pk, r['is_chair'], {f: r[f] for f in ctx['perm_fields']})
                          for r in ctx['member_rows']])

    def test_set_and_reset(self):
        r = self.post(f'members/{self.outsider.pk}/', {'can_grade_submissions': True}, base=self.P)
        self.assertEqual(r.status_code, 200, r.content)
        perm = EducationMemberPermission.objects.get(user=self.outsider)
        self.assertEqual((perm.can_grade_submissions, perm.can_view_submissions, perm.granted_by),
                         (True, False, self.chair))
        # He now reaches the dashboard.
        self.assertEqual(self.get('', self.outsider).status_code, 200)
        self.assertRefused(self.post(f'members/{self.chair.pk}/', {}, base=self.P), 400, 'invalid_input')
        self.assertEqual(self.post(f'members/nobody/', {}, base=self.P).status_code, 404)
        r = self.post('reset/', base=self.P)
        self.assertEqual(r.json()['deleted'], 3)
        self.assertFalse(EducationMemberPermission.objects.exists())
