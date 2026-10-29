"""
Mobile plan, Phase 3d — My Excuses and Polls.

The endpoints must apply the rules the web pages apply (my_excuses /
submit_excuse / cancel_excuse, polls_view / take_poll): the same events on
offer, the same refusals, and one response row per member that is updated in
place when they change their answer.

    python manage.py test src.tests.mobile.test_excuses_and_polls
"""
import json
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollOption, AnnouncementPollQuestion, AnnouncementPollResponse,
    AttendanceExcuse, Event,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('ex-1')
        _, self.access, _ = make_device(self.user)
        self.officer = make_user('ex-off', member_type='Officer')
        self.api = Client()

    def get(self, path, **params):
        return self.api.get(API + path, params, **bearer(self.access))

    def post(self, path, body=None):
        return self.api.post(API + path, json.dumps(body or {}), content_type='application/json',
                             **bearer(self.access))


class ExcuseTests(_Base):

    def event(self, days=2, **extra):
        fields = dict(title=f'Meeting {days}', description='d', created_by=self.officer, requires_attendance=True,
                      allow_excuses=True, date_time=timezone.now() + timedelta(days=days))
        fields.update(extra)
        return Event.objects.create(**fields)

    def test_list_counts_and_available(self):
        open_event = self.event(2)
        done = self.event(4)
        self.event(6, allow_excuses=False)
        self.event(8, excuse_deadline=timezone.now() - timedelta(hours=1))
        AttendanceExcuse.objects.create(event=done, user=self.user, reason='Exam week', status='approved',
                                        review_notes='OK')
        body = self.get('my/excuses/').json()
        self.assertEqual(body['counts'], {'pending': 0, 'approved': 1, 'denied': 0, 'total': 1})
        self.assertEqual([e['id'] for e in body['available']], [open_event.id])
        self.assertEqual(body['excuses'][0]['review_notes'], 'OK')

    def test_submit_and_cancel(self):
        e = self.event()
        self.assertEqual(self.post('my/excuses/', {'event_id': e.id, 'reason': 'short'}).json()['error'],
                         'reason_too_short')
        r = self.post('my/excuses/', {'event_id': e.id, 'reason': 'Family emergency at home'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['status'], 'pending')
        self.assertEqual(self.post('my/excuses/', {'event_id': e.id, 'reason': 'Family emergency at home'})
                         .json()['error'], 'already_submitted')
        excuse_id = r.json()['id']
        self.assertTrue(self.post(f'my/excuses/{excuse_id}/cancel/').json()['ok'])
        self.assertFalse(AttendanceExcuse.objects.filter(id=excuse_id).exists())

    def test_refusals(self):
        late = self.event(2, excuse_deadline=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.post('my/excuses/', {'event_id': late.id, 'reason': 'Family emergency'})
                         .json()['error'], 'deadline_passed')
        closed = self.event(3, allow_excuses=False)
        self.assertEqual(self.post('my/excuses/', {'event_id': closed.id, 'reason': 'Family emergency'})
                         .json()['error'], 'excuses_not_allowed')
        self.assertEqual(self.post('my/excuses/', {'event_id': 999999, 'reason': 'Family emergency'})
                         .status_code, 404)

    def test_cannot_cancel_reviewed_or_others(self):
        e = self.event()
        mine = AttendanceExcuse.objects.create(event=e, user=self.user, reason='Exam week', status='denied')
        self.assertEqual(self.post(f'my/excuses/{mine.id}/cancel/').json()['error'], 'not_pending')
        theirs = AttendanceExcuse.objects.create(event=e, user=self.officer, reason='Exam week')
        self.assertEqual(self.post(f'my/excuses/{theirs.id}/cancel/').status_code, 404)

    def test_flag_off(self):
        flag('excuse_system', False)
        self.assertEqual(self.get('my/excuses/').json()['error'], 'feature_disabled')


class PollTests(_Base):

    def poll(self, visible_to=None, **extra):
        a = Announcement.objects.create(title='Formal', content='c', posted_by=self.officer,
                                        visible_to=visible_to or [])
        poll = AnnouncementPoll.objects.create(announcement=a, title='Venue', **extra)
        self.single = AnnouncementPollQuestion.objects.create(poll=poll, text='Where?', question_type='single')
        self.a = AnnouncementPollOption.objects.create(question=self.single, text='Lake')
        self.b = AnnouncementPollOption.objects.create(question=self.single, text='Hotel')
        self.text = AnnouncementPollQuestion.objects.create(poll=poll, text='Notes', question_type='text',
                                                            is_required=False, order=1)
        return a

    def test_list_split_open_closed(self):
        open_a = self.poll()
        closed_a = self.poll(is_open=False)
        self.poll(visible_to=['Officer'])
        body = self.get('polls/').json()
        self.assertEqual([p['id'] for p in body['open']], [open_a.id])
        self.assertEqual([p['id'] for p in body['closed']], [closed_a.id])

    def test_answer_and_change(self):
        a = self.poll()
        self.assertEqual(self.post(f'polls/{a.id}/', {'answers': {}}).json()['error'], 'answer_required')
        r = self.post(f'polls/{a.id}/', {'answers': {str(self.single.id): [self.a.id], str(self.text.id): 'Hi'}})
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()['responded'])
        r = self.post(f'polls/{a.id}/', {'answers': {str(self.single.id): [self.b.id, self.a.id]}})
        self.assertEqual(r.status_code, 200)
        body = self.get(f'polls/{a.id}/').json()
        self.assertTrue(body['edited'])
        self.assertEqual(body['questions'][0]['answer']['options'], [self.b.id])   # single choice keeps one
        self.assertEqual(body['questions'][1]['answer']['text'], '')
        self.assertEqual(AnnouncementPollResponse.objects.filter(respondent=self.user).count(), 1)

    def test_closed_and_invisible(self):
        closed = self.poll(is_open=False)
        self.assertEqual(self.post(f'polls/{closed.id}/', {'answers': {str(self.single.id): [self.a.id]}})
                         .json()['error'], 'poll_closed')
        hidden = self.poll(visible_to=['Officer'])
        self.assertEqual(self.get(f'polls/{hidden.id}/').status_code, 404)

    def test_bad_option_ignored(self):
        a = self.poll()
        other = self.poll()
        r = self.post(f'polls/{a.id}/', {'answers': {str(self.single.id): [self.a.id + 999]}})
        self.assertEqual(r.json()['error'], 'answer_required')
        self.assertFalse(AnnouncementPollResponse.objects.filter(poll__announcement=other).exists())
