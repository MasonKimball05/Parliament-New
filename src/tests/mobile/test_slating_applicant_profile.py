"""
11-10-26: the app's application screen gets what the web page now shows:
whole interviews (strengths, concerns, notes; None once destroyed) and a
`profile` block: recommended positions, slate spots, nominations, history.
"""
from django.utils import timezone

from src.models import SlatingActivity, SlatingInterview, SlatingPosition
from src.tests.mobile.test_slating_batch5 import _AppBase


class AppApplicantProfileTests(_AppBase):

    def setUp(self):
        super().setUp()
        self.treas = SlatingPosition.objects.create(period=self.period, title='Treasurer', code='T')
        self.app.position_preferences = {'first_choice': [self.pres.id], 'second_choice': [self.vp.id],
                                         'third_choice': [], 'do_not_want': [self.treas.id]}
        self.app.save()
        self.path = f'slating/periods/{self.period.id}/applications/{self.app.id}/'

    def body(self):
        r = self.call('get', self.path, user=self.committee_member)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_whole_interview_and_profile(self):
        i = SlatingInterview.objects.create(application=self.app, completed_at=timezone.now(), location='Room 2',
                                            notes='N', strengths='S', concerns='C', recommendation='recommend')
        i.interviewers.add(self.manager)
        i.recommended_positions.set([self.pres, self.treas])
        SlatingActivity.objects.create(period=self.period, user=self.manager, action='application_reviewed',
                                       details='Reviewed', metadata={'application_id': self.app.id})
        b = self.body()
        iv = b['interviews'][0]
        self.assertEqual((iv['notes'], iv['strengths'], iv['concerns'], iv['location']), ('N', 'S', 'C', 'Room 2'))
        self.assertEqual([m['user_id'] for m in iv['interviewers']], [self.manager.user_id])
        p = b['profile']
        self.assertEqual(p['recommended_position_ids'], sorted([self.pres.id, self.treas.id]))
        self.assertTrue(p['any_positions_marked'])
        self.assertEqual(p['extra_recommended'], [{'id': self.treas.id, 'title': 'Treasurer', 'do_not_want': True}])
        self.assertEqual(p['latest_recommendation'], 'Recommend')
        self.assertEqual(p['slate_spots'][0]['position'], 'President')
        self.assertEqual(p['history'][0]['details'], 'Reviewed')

    def test_destroyed_notes_are_null(self):
        i = SlatingInterview.objects.create(application=self.app, completed_at=timezone.now(),
                                            notes='SECRET', strengths='SECRET', concerns='SECRET')
        i.destroy_notes(self.manager)
        iv = self.body()['interviews'][0]
        self.assertEqual((iv['notes'], iv['strengths'], iv['concerns']), (None, None, None))
        self.assertTrue(iv['notes_destroyed'])

    def test_still_committee_only(self):
        self.assertEqual(self.call('get', self.path).status_code, 403)

    def test_whole_committee_flag(self):
        i = SlatingInterview.objects.create(application=self.app, scheduled_at=timezone.now())
        i.interviewers.set([self.committee_member])
        self.assertTrue(self.body()['interviews'][0]['whole_committee'])
        r = self.call('get', f'slating/periods/{self.period.id}/interviews/', user=self.committee_member)
        self.assertTrue(r.json()['interviews'][0]['whole_committee'])
