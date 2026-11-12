"""
11-12-26: the record-interview page saves a draft in the background
(slating_interview_draft) so a dropped connection can't lose the notes. A
draft doesn't complete the interview or log anything, refuses to overwrite
someone else's newer save, and has the form's own access rules.
"""
from django.urls import reverse

from src.models import SlatingActivity, SlatingInterview
from src.tests.slating.test_slating_review_fixes import _Base


class InterviewDraftTests(_Base):

    def setUp(self):
        super().setUp()
        self.interview = SlatingInterview.objects.create(application=self.app)
        self.url = reverse('slating_interview_draft', args=[self.interview.id])
        self.client.force_login(self.committee_member)

    def post(self, **extra):
        data = {'notes': 'Pages of notes', 'strengths': 'Prepared', 'concerns': '', 'recommendation': 'recommend',
                'recommended_positions': [str(self.pres.id)],
                'base_updated': self.interview.updated_at.isoformat(), **extra}
        return self.client.post(self.url, data)

    def test_saves_the_fields_without_completing(self):
        r = self.post()
        self.assertEqual(r.status_code, 200, r.content)
        self.interview.refresh_from_db()
        self.assertEqual((self.interview.notes, self.interview.strengths, self.interview.recommendation),
                         ('Pages of notes', 'Prepared', 'recommend'))
        self.assertEqual(list(self.interview.recommended_positions.all()), [self.pres])
        self.assertIsNone(self.interview.completed_at)
        self.assertFalse(SlatingActivity.objects.filter(action='interview_completed').exists())
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'submitted')
        self.assertEqual(r.json()['updated'], self.interview.updated_at.isoformat())

    def test_successive_drafts_chain_on_the_new_timestamp(self):
        first = self.post().json()['updated']
        r = self.post(notes='More notes', base_updated=first)
        self.assertEqual(r.status_code, 200)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.notes, 'More notes')

    def test_someone_elses_newer_save_is_not_overwritten(self):
        stale = self.interview.updated_at.isoformat()
        SlatingInterview.objects.filter(pk=self.interview.pk).update(notes='Their notes')
        self.interview.refresh_from_db()
        self.interview.save()                       # bumps updated_at, as their save would
        r = self.post(base_updated=stale)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()['conflict'])
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.notes, 'Their notes')

    def test_access_matches_the_form(self):
        self.client.force_login(self.voter)          # the applicant: no committee access
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(self.admin)          # site admin on a locked period
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_destroyed_notes_are_not_brought_back(self):
        self.interview.destroy_notes(self.manager)
        r = self.post()
        self.assertEqual(r.status_code, 409)
        self.interview.refresh_from_db()
        self.assertTrue(self.interview.notes.startswith('[DESTROYED'))

    def test_form_carries_the_hooks(self):
        r = self.client.get(reverse('slating_complete_interview', args=[self.interview.id]))
        self.assertContains(r, 'id="interview-notes-form"')
        self.assertContains(r, self.url)
        self.assertContains(r, 'js/slating/interview-notes.js')


from src.models import SlatingInterview as _SI  # noqa: E402
from src.tests.mobile.test_slating_batch5 import _AppBase  # noqa: E402


class AppInterviewDraftTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.interview = _SI.objects.create(application=self.app)
        self.path = f'slating/interviews/{self.interview.id}/'

    def test_payload_has_updated_and_draft_saves(self):
        b = self.call('get', self.path, user=self.committee_member).json()
        r = self.call('post', self.path + 'draft/', {'notes': 'From the phone', 'strengths': '', 'concerns': '',
                                                    'recommendation': '', 'recommended_positions': [],
                                                    'base_updated': b['updated']}, user=self.committee_member)
        self.assertEqual(r.status_code, 200, r.content)
        self.interview.refresh_from_db()
        self.assertEqual(self.interview.notes, 'From the phone')
        self.assertIsNone(self.interview.completed_at)
        stale = self.call('post', self.path + 'draft/', {'notes': 'x', 'base_updated': b['updated']},
                          user=self.committee_member)
        self.assertEqual((stale.status_code, stale.json()['error']), (409, 'conflict'))
        self.assertEqual(self.call('post', self.path + 'draft/', {'notes': 'x'}).status_code, 403)
