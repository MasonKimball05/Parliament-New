"""
Meeting agendas and a pledge's My Tasks in the app (10-30-26;
src/mobile/views_agendas.py, src/mobile/views_pledge.py): the website's rules,
through the same helpers.

    python manage.py test src.tests.mobile.test_agendas_and_pledge_tasks
"""
import json
from datetime import date, timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    AgendaItem, Committee, EducationAbsenceRequest, EducationMeeting, Event, MeetingAgenda, MinutesSection, PledgeQuizAnswer,
    PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag


def url(name, *args):
    return reverse(f'mobile:{name}', args=args)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()

    def call(self, method, path, access, body=None):
        fn = getattr(self.api, method)
        if body is None:
            return fn(path, **bearer(access))
        return fn(path, json.dumps(body), content_type='application/json', **bearer(access))


class AgendaTests(_Base):

    def setUp(self):
        super().setUp()
        flag('meeting_agendas', True)
        self.officer = make_user('ag-off', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.member = make_user('ag-mem')
        _, self.member_access, _ = make_device(self.member)
        r = self.call('post', url('mobile_agendas'), self.officer_access, {
            'title': 'Chapter, week 10', 'date': (date.today() + timedelta(days=3)).isoformat(),
            'start_time': '19:00', 'standard_order': True})
        self.assertEqual(r.status_code, 201, r.content)
        self.id = r.json()['agenda']['id']

    def test_create_with_the_standard_order(self):
        agenda = MeetingAgenda.objects.get(pk=self.id)
        self.assertEqual([i.item_type for i in agenda.items.all()], AgendaItem.STANDARD_ORDER)
        self.assertEqual(agenda.created_by, self.officer)
        r = self.call('post', url('mobile_agendas'), self.officer_access, {'title': 'x', 'date': 'soon'})
        self.assertEqual(r.status_code, 400)

    def test_members_see_published_agendas_only(self):
        listed = self.call('get', url('mobile_agendas'), self.member_access).json()
        self.assertEqual(listed['upcoming'], [])
        self.assertEqual(listed['events'], [])
        self.assertEqual(self.call('get', url('mobile_agenda', self.id), self.member_access).status_code, 404)
        self.assertEqual(self.call('post', url('mobile_agenda_status', self.id), self.officer_access,
                                   {'status': 'published'}).status_code, 200)
        listed = self.call('get', url('mobile_agendas'), self.member_access).json()
        self.assertEqual([a['id'] for a in listed['upcoming']], [self.id])
        body = self.call('get', url('mobile_agenda', self.id), self.member_access).json()
        self.assertEqual(len(body['items']), len(AgendaItem.STANDARD_ORDER))
        self.assertNotIn('members', body)   # the presenter picker is the editor's

    def test_members_cannot_edit(self):
        item = MeetingAgenda.objects.get(pk=self.id).items.first()
        for method, path, body in (
                ('post', url('mobile_agendas'), {'title': 'x', 'date': '2026-11-01', 'start_time': '19:00'}),
                ('post', url('mobile_agenda_items', self.id), {'title': 'x'}),
                ('patch', url('mobile_agenda_item', self.id, item.id), {'title': 'x'}),
                ('delete', url('mobile_agenda_item', self.id, item.id), None),
                ('post', url('mobile_agenda_item_move', self.id, item.id), {'direction': 'down'}),
                ('post', url('mobile_agenda_status', self.id), {'status': 'published'}),
                ('post', url('mobile_agenda_start_minutes', self.id), {})):
            self.assertEqual(self.call(method, path, self.member_access, body).status_code, 403, path)

    def test_items_add_edit_move_delete(self):
        r = self.call('post', url('mobile_agenda_items', self.id), self.officer_access, {
            'item_type': 'new_business', 'title': 'Dues', 'presenter': self.member.user_id,
            'notes': 'Due Friday.', 'duration_minutes': 5})
        self.assertEqual(r.status_code, 201, r.content)
        new = r.json()['item']
        self.assertEqual((new['presenter_display'], new['duration_minutes']), (self.member.get_display_name(), 5))
        self.assertEqual(r.json()['items'][-1]['id'], new['id'])
        # A PATCH keeps what it doesn't name; a bad type falls back to Other.
        r = self.call('patch', url('mobile_agenda_item', self.id, new['id']), self.officer_access,
                      {'title': 'Dues and fines', 'item_type': 'bogus'})
        item = AgendaItem.objects.get(pk=new['id'])
        self.assertEqual((item.title, item.notes, item.presenter, item.item_type),
                         ('Dues and fines', 'Due Friday.', self.member, 'custom'))
        r = self.call('post', url('mobile_agenda_item_move', self.id, item.id), self.officer_access,
                      {'direction': 'up'})
        self.assertEqual([i['id'] for i in r.json()['items']][-2], item.id)
        self.assertEqual(self.call('post', url('mobile_agenda_item_move', self.id, item.id), self.officer_access,
                                   {'direction': 'sideways'}).status_code, 400)
        self.call('delete', url('mobile_agenda_item', self.id, item.id), self.officer_access)
        self.assertFalse(AgendaItem.objects.filter(pk=item.id).exists())

    def test_start_minutes_once(self):
        r = self.call('post', url('mobile_agenda_start_minutes', self.id), self.officer_access, {})
        self.assertEqual(r.status_code, 201, r.content)
        minutes_id = r.json()['minutes_id']
        headers = MinutesSection.objects.filter(minutes_id=minutes_id, section_type='header')
        self.assertEqual(headers.count(), len(AgendaItem.STANDARD_ORDER))
        again = self.call('post', url('mobile_agenda_start_minutes', self.id), self.officer_access, {})
        self.assertEqual((again.status_code, again.json()['minutes_id']), (200, minutes_id))
        # The app opens it in the minutes editor.
        self.assertEqual(self.call('get', url('mobile_officer_minutes_detail', minutes_id),
                                   self.officer_access).status_code, 200)

    def test_flag_off(self):
        flag('meeting_agendas', False)
        r = self.call('get', url('mobile_agendas'), self.member_access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        me = self.call('get', url('mobile_me'), self.member_access).json()
        self.assertFalse(me['features']['meeting_agendas'])

    def test_website_still_works(self):
        """The web views call the same helpers."""
        self.client.force_login(self.officer)
        r = self.client.post(reverse('add_agenda_item', args=[self.id]), {'title': 'From the web'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AgendaItem.objects.filter(agenda_id=self.id, title='From the web').exists())
        r = self.client.post(reverse('start_minutes_from_agenda', args=[self.id]))
        self.assertRedirects(r, reverse('edit_chapter_minutes', args=[MeetingAgenda.objects.get(pk=self.id).minutes_id]),
                             fetch_redirect_response=False)


class PledgeTaskTests(_Base):

    def setUp(self):
        super().setUp()
        self.pledge = make_user('pt-p1', member_type='Pledge')
        _, self.access, _ = make_device(self.pledge)
        self.member = make_user('pt-mem')
        _, self.member_access, _ = make_device(self.member)
        self.task = PledgeTask.objects.create(title='Learn the creed', points=10, is_required=True)
        self.quiz = PledgeTask.objects.create(title='History quiz', task_type='quiz', points=5)
        self.q1 = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Founded when?', display_order=1)
        self.q2 = PledgeTaskQuestion.objects.create(task=self.quiz, question_text='Founded where?', display_order=2)
        self.other = make_user('pt-p2', member_type='Pledge')

    def test_pledges_only(self):
        for path in (url('mobile_pledge_tasks'), url('mobile_pledge_quiz', self.quiz.id)):
            r = self.call('get', path, self.member_access)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'), path)

    def test_my_tasks(self):
        PledgeTaskCompletion.objects.create(task=self.task, pledge=self.pledge, status='completed')
        hidden = PledgeTask.objects.create(title='Not yours', points=1)
        hidden.assigned_to.add(self.other)
        body = self.call('get', url('mobile_pledge_tasks'), self.access).json()
        titles = [t['title'] for g in body['phases'] for t in g['tasks']]
        self.assertIn('Learn the creed', titles)
        self.assertNotIn('Not yours', titles)
        self.assertEqual(body['progress']['required_done'], 1)
        self.assertEqual(body['points']['tasks'], 10)
        self.assertIsNone(body['big'])

    def test_take_a_quiz(self):
        r = self.call('get', url('mobile_pledge_quiz', self.quiz.id), self.access)
        self.assertEqual([q['number'] for q in r.json()['questions']], [1, 2])
        r = self.call('post', url('mobile_pledge_quiz', self.quiz.id), self.access,
                      {'answers': {str(self.q1.id): '1906'}})
        self.assertEqual((r.status_code, r.json()['detail']), (400, 'Please answer question 2.'))
        self.assertFalse(PledgeQuizAnswer.objects.exists())   # nothing saved on an error
        r = self.call('post', url('mobile_pledge_quiz', self.quiz.id), self.access,
                      {'answers': {str(self.q1.id): '1906', str(self.q2.id): 'Cornell'}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['submitted'])
        self.assertEqual(PledgeTaskCompletion.objects.get(task=self.quiz, pledge=self.pledge).status, 'pending')
        again = self.call('post', url('mobile_pledge_quiz', self.quiz.id), self.access,
                          {'answers': {str(self.q1.id): 'x', str(self.q2.id): 'y'}})
        self.assertEqual(again.json()['error'], 'already_submitted')

    def test_a_quiz_for_someone_else_is_not_found(self):
        self.quiz.assigned_to.add(self.other)
        for path in (url('mobile_pledge_quiz', self.quiz.id), url('mobile_pledge_quiz_analysis', self.quiz.id)):
            self.assertEqual(self.call('get', path, self.access).status_code, 404, path)

    def test_analysis_needs_the_chair_to_share_it(self):
        self.assertEqual(self.call('get', url('mobile_pledge_quiz_analysis', self.quiz.id),
                                   self.access).status_code, 404)
        self.quiz.show_analysis_to_pledges = True
        self.quiz.save(update_fields=['show_analysis_to_pledges'])
        PledgeQuizAnswer.objects.create(question=self.q1, pledge=self.other, answer_text='1906', is_correct=True)
        body = self.call('get', url('mobile_pledge_quiz_analysis', self.quiz.id), self.access).json()
        self.assertTrue(body['withheld'])   # one submission: too few to show
        self.assertEqual(body['questions'], [])

    def test_absence_request(self):
        chair = make_user('pt-chair')
        soon = Event.objects.create(title='Pledge meeting', description='d', created_by=chair,
                                    date_time=timezone.now() + timedelta(days=2))
        past = Event.objects.create(title='Last week', description='d', created_by=chair,
                                    date_time=timezone.now() - timedelta(days=5))
        edu = Committee.objects.create(name='Education', code='EDU', is_education_committee=True, is_active=True)
        meeting = EducationMeeting.objects.create(event=soon, committee=edu, points=5)
        old = EducationMeeting.objects.create(event=past, committee=edu, points=5)
        r = self.call('post', url('mobile_pledge_absence', meeting.id), self.access, {'reason': ''})
        self.assertEqual(r.status_code, 400)
        r = self.call('post', url('mobile_pledge_absence', old.id), self.access, {'reason': 'Exam'})
        self.assertEqual(r.status_code, 400)
        r = self.call('post', url('mobile_pledge_absence', meeting.id), self.access, {'reason': 'Exam'})
        self.assertEqual((r.status_code, r.json()['absence_request']['status']), (201, 'pending'))
        self.assertEqual(EducationAbsenceRequest.objects.get(pledge=self.pledge).reason, 'Exam')
        body = self.call('get', url('mobile_pledge_tasks'), self.access).json()
        self.assertEqual(body['upcoming_meetings'][0]['absence_request']['reason'], 'Exam')

    def test_website_quiz_still_works(self):
        self.client.force_login(self.pledge)
        r = self.client.post(reverse('pledge_take_quiz', args=[self.quiz.id]),
                             {f'answer_{self.q1.id}': 'a', f'answer_{self.q2.id}': ''})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Please answer question 2.')
        r = self.client.post(reverse('pledge_take_quiz', args=[self.quiz.id]),
                             {f'answer_{self.q1.id}': 'a', f'answer_{self.q2.id}': 'b'})
        self.assertRedirects(r, reverse('my_pledge_tasks'), fetch_redirect_response=False)
        self.assertEqual(PledgeQuizAnswer.objects.filter(pledge=self.pledge).count(), 2)
