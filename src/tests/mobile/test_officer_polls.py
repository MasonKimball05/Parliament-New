"""
Polls on announcements (10-30-26): src/poll_editing.py, shared by the website's
create_or_edit_poll / poll_results and the app's officer endpoints.

    python manage.py test src.tests.mobile.test_officer_polls
"""
from django.urls import reverse

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollAnswer, AnnouncementPollResponse,
)
from src.tests.mobile.sess_helpers import make_user
from src.tests.mobile.test_officer import _Base


class PollEditingTests(_Base):

    def setUp(self):
        super().setUp()
        self.a = Announcement.objects.create(title='Dues', content='x', posted_by=self.officer)

    def test_website_form_creates_and_edits(self):
        self.client.force_login(self.officer)
        url = reverse('create_or_edit_poll', args=[self.a.id])
        self.client.post(url, {
            'poll_title': 'Formal', 'is_open': 'on',
            'question_text_0': 'Coming?', 'question_type_0': 'single', 'question_required_0': 'on',
            'option_text_0_0': 'Yes', 'option_text_0_1': 'No', 'option_text_0_2': '',
            'question_text_1': 'Notes', 'question_type_1': 'text',
        })
        poll = AnnouncementPoll.objects.get(announcement=self.a)
        qs = list(poll.questions.order_by('order'))
        self.assertEqual([q.text for q in qs], ['Coming?', 'Notes'])
        self.assertEqual([o.text for o in qs[0].options.order_by('order')], ['Yes', 'No'])
        yes, no = qs[0].options.order_by('order')
        # Edit: keep the first question (rename an option), drop the second.
        self.client.post(url, {
            'poll_title': 'Formal', 'is_open': 'on',
            'question_id_0': qs[0].id, 'question_text_0': 'Coming?', 'question_type_0': 'single',
            'option_id_0_0': yes.id, 'option_text_0_0': 'Yes!', 'option_id_0_1': no.id, 'option_text_0_1': 'No',
        })
        self.assertEqual(list(poll.questions.values_list('id', flat=True)), [qs[0].id])
        yes.refresh_from_db()
        self.assertEqual(yes.text, 'Yes!')
        self.assertEqual(self.client.get(reverse('poll_results', args=[self.a.id])).status_code, 200)

    def test_app_endpoints(self):
        path = f'officer/announcements/{self.a.id}/poll/'
        self.assertIsNone(self.call('get', path).json()['poll'])
        self.assertEqual(self.call('put', path, {'title': ''}).status_code, 400)
        body = {'title': 'Food', 'is_open': True, 'is_anonymous': True, 'questions': [
            {'text': 'Pizza or tacos?', 'type': 'single', 'required': True,
             'options': [{'text': 'Pizza'}, {'text': 'Tacos'}]}]}
        poll = self.call('put', path, body).json()['poll']
        self.assertTrue(poll['is_anonymous'])
        q = poll['questions'][0]
        self.assertEqual([o['text'] for o in q['options']], ['Pizza', 'Tacos'])
        # Anonymity sticks.
        self.assertTrue(self.call('put', path, {**body, 'is_anonymous': False,
                                                'questions': [{**q}]}).json()['poll']['is_anonymous'])

        # Results: anonymous with one response hides who answered.
        p = AnnouncementPoll.objects.get(announcement=self.a)
        r = AnnouncementPollResponse.objects.create(poll=p, respondent=self.member)
        ans = AnnouncementPollAnswer.objects.create(response=r, question_id=q['id'])
        ans.selected_options.add(q['options'][0]['id'])
        res = self.call('get', path + 'results/').json()
        self.assertEqual(res['respondent_count'], 1)
        self.assertEqual(res['questions'][0]['options'][0], {'text': 'Pizza', 'count': 1})
        self.assertIsNone(res['responses'])
        self.assertIsNone(res['non_respondents'])

        self.assertEqual(self.call('get', path, access=self.member_access).status_code, 403)
        self.call('delete', path)
        self.assertFalse(AnnouncementPoll.objects.filter(announcement=self.a).exists())

    def test_named_results_when_not_anonymous(self):
        path = f'officer/announcements/{self.a.id}/poll/'
        q = self.call('put', path, {'title': 'Ride', 'is_open': True, 'questions': [
            {'text': 'Driving?', 'type': 'text'}]}).json()['poll']['questions'][0]
        p = AnnouncementPoll.objects.get(announcement=self.a)
        r = AnnouncementPollResponse.objects.create(poll=p, respondent=self.member)
        AnnouncementPollAnswer.objects.create(response=r, question_id=q['id'], text_answer='Yes, 3 seats')
        make_user('someone-else')
        res = self.call('get', path + 'results/').json()
        self.assertEqual(res['responses'][0]['respondent']['user_id'], 'mem-1')
        self.assertEqual(res['questions'][0]['text_answers'], ['Yes, 3 seats'])
        self.assertIn('someone-else', [u['user_id'] for u in res['non_respondents']])
