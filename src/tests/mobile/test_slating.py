"""
Mobile plan, Phase 3i — the member side of Slating in the app.

The app votes through ``src/slating_ballots.py`` like the web: the same
eligibility order, re-auth, one ballot per round, and a vote that can't be
traced to the voter.

    python manage.py test src.tests.mobile.test_slating
"""
import json

from django.test import Client, override_settings

from src.models import SlatingActivity, SlatingApplication, SlatingBallot, SlatingVote
from src.tests.mobile.sess_helpers import bearer, make_device
from src.tests.slating.test_slating_review_fixes import PW, _Base, member

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class SlatingAppTests(_Base):

    def setUp(self):
        super().setUp()
        _, self.access, _ = make_device(self.voter)
        self.api = Client()

    def call(self, method, path, body=None, access=None):
        kw = bearer(access or self.access)
        if body is None:
            return getattr(self.api, method)(API + path, **kw)
        return getattr(self.api, method)(API + path, json.dumps(body), content_type='application/json', **kw)

    def vote_path(self):
        return f'slating/periods/{self.period.id}/vote/'

    def test_hub(self):
        body = self.call('get', 'slating/').json()
        p = body['periods'][0]
        self.assertEqual((p['status'], p['voting_open'], p['can_vote'], p['has_voted']),
                         ('voting_open', True, True, False))
        self.assertEqual(body['applications'][0]['status'], 'submitted')
        self.assertTrue(body['applications'][0]['can_withdraw'])

    def test_slate_vote_needs_reauth_then_records_once(self):
        ballot = self.call('get', self.vote_path()).json()
        self.assertEqual([c['position'] for c in ballot['candidates']], ['President', 'VP'])
        self.assertFalse(ballot['has_voted'])

        r = self.call('post', self.vote_path(), {'choice': 'approve'})
        self.assertEqual((r.status_code, r.json()['error']), (401, 'reauth_required'))
        r = self.call('post', self.vote_path(), {'choice': 'approve', 'password': 'wrong'})
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertFalse(SlatingBallot.objects.exists())

        r = self.call('post', self.vote_path(), {'choice': 'reject', 'password': PW, 'rejected_positions': []})
        self.assertEqual(r.json()['error'], 'no_positions')
        r = self.call('post', self.vote_path(),
                      {'choice': 'reject', 'password': PW, 'rejected_positions': [self.vp.id, 99999]})
        self.assertEqual(r.status_code, 200, r.content)
        vote = SlatingVote.objects.get()
        self.assertEqual((vote.vote_choice, vote.rejected_positions), ('reject', [self.vp.id]))
        self.assertEqual(SlatingBallot.objects.get().voter, self.voter)
        self.assertTrue(SlatingActivity.objects.filter(action='vote_cast', user=self.voter).exists())

        r = self.call('post', self.vote_path(), {'choice': 'approve', 'password': PW})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_voted'))
        self.assertTrue(self.call('get', 'slating/').json()['periods'][0]['has_voted'])

    def test_eligibility_comes_before_reauth(self):
        absent = member('sr-absent')
        _, access, _ = make_device(absent)
        r = self.call('post', self.vote_path(), {'choice': 'approve'}, access=access)
        self.assertEqual(r.json()['error'], 'not_present')
        pledge = member('sr-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        self.assertEqual(self.call('get', self.vote_path(), access=access).json()['error'], 'not_eligible')
        self.period.status = 'deliberation'
        self.period.save()
        self.assertEqual(self.call('get', self.vote_path()).json()['error'], 'voting_closed')

    def test_individual_vote(self):
        self.period.vote_type = 'individual'
        self.period.save()
        ballot = self.call('get', self.vote_path()).json()
        self.assertEqual([p['position'] for p in ballot['positions']], ['President', 'VP'])
        r = self.call('post', self.vote_path(), {'password': PW, 'choices': {str(self.c_pres.id): 'approve'}})
        self.assertEqual(r.json()['error'], 'invalid_choice')          # VP left blank
        r = self.call('post', self.vote_path(), {'password': PW, 'choices': {
            str(self.c_pres.id): 'approve', str(self.c_vp.id): 'reject'}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(SlatingBallot.objects.filter(vote_type='individual').count(), 2)
        self.assertTrue(all(p['voted'] for p in self.call('get', self.vote_path()).json()['positions']))
        self.assertTrue(self.call('get', 'slating/').json()['periods'][0]['has_voted'])
        r = self.call('post', self.vote_path(), {'password': PW, 'choices': {}})
        self.assertEqual(r.json()['error'], 'already_voted')

    def test_withdraw(self):
        r = self.call('post', f'slating/applications/{self.app.id}/withdraw/', {})
        self.assertEqual(r.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'withdrawn')
        r = self.call('post', f'slating/applications/{self.app.id}/withdraw/', {})
        self.assertEqual(r.json()['error'], 'not_withdrawable')
        other = SlatingApplication.objects.create(period=self.period, applicant=self.manager, status='submitted')
        self.assertEqual(self.call('post', f'slating/applications/{other.id}/withdraw/', {}).status_code, 404)

    def test_results_only_when_published(self):
        path = f'slating/periods/{self.period.id}/results/'
        self.assertEqual(self.call('get', path).json()['error'], 'not_published')
        self.call('post', self.vote_path(), {'choice': 'approve', 'password': PW})
        self.period.status = 'results_published'
        self.period.save()
        body = self.call('get', path).json()
        self.assertEqual((body['slate']['approve'], body['slate']['passed']), (1, True))
        self.assertEqual([c['position'] for c in body['candidates']], ['President', 'VP'])


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class RunoffTests(_Base):
    """
    A failed round, closed and reopened by the chair on the website (the real
    close_voting / change_period_status views), is a new round in the app:
    members vote again, and an individual vote puts only the failed positions
    back on the ballot.
    """

    def setUp(self):
        super().setUp()
        from src.models import SlatingAttendance
        self.voter2 = member('sr-voter2')
        SlatingAttendance.objects.create(period=self.period, member=self.voter2)
        self.api = Client()
        self.access = make_device(self.voter)[1]
        self.access2 = make_device(self.voter2)[1]
        self.chair = Client()
        self.chair.force_login(self.manager)

    def cast(self, access, body):
        return self.api.post(f'{API}slating/periods/{self.period.id}/vote/', json.dumps({**body, 'password': PW}),
                             content_type='application/json', **bearer(access))

    def ballot(self, access):
        return self.api.get(f'{API}slating/periods/{self.period.id}/vote/', **bearer(access)).json()

    def close_and_reopen(self, vote_type):
        from django.urls import reverse
        self.chair.post(reverse('slating_close_voting', args=[self.period.id]))
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'deliberation')
        self.chair.post(reverse('slating_change_status', args=[self.period.id]),
                        {'status': 'voting_open', 'vote_type': vote_type})
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'voting_open')

    def test_failed_slate_round_is_voted_again(self):
        for access in (self.access, self.access2):
            self.assertEqual(self.cast(access, {'choice': 'reject', 'rejected_positions': [self.pres.id]}).status_code, 200)
        self.assertEqual(self.cast(self.access, {'choice': 'approve'}).json()['error'], 'already_voted')

        self.close_and_reopen('slate')
        self.assertEqual(self.period.current_voting_attempt, 2)
        b = self.ballot(self.access)
        self.assertEqual((b['attempt'], b['has_voted']), (2, False))          # a fresh round
        self.assertEqual(self.cast(self.access, {'choice': 'approve'}).status_code, 200)
        self.assertTrue(self.ballot(self.access)['has_voted'])
        self.assertEqual(SlatingVote.objects.filter(voting_attempt=2).count(), 1)
        self.assertEqual(SlatingVote.objects.filter(voting_attempt=1).count(), 2)   # round 1 kept

    def test_individual_runoff_only_failed_positions(self):
        self.period.vote_type = 'individual'
        self.period.save()
        pres, vp = str(self.c_pres.id), str(self.c_vp.id)
        for access in (self.access, self.access2):
            r = self.cast(access, {'choices': {pres: 'approve', vp: 'reject'}})
            self.assertEqual(r.status_code, 200, r.content)

        self.close_and_reopen('individual')
        self.c_pres.refresh_from_db()
        self.c_vp.refresh_from_db()
        self.assertTrue(self.c_pres.individual_passed)
        self.assertIsNone(self.c_vp.individual_passed)                      # back on the ballot

        b = self.ballot(self.access)
        self.assertEqual(b['attempt'], 2)
        self.assertEqual([(p['position'], p['voted']) for p in b['positions']], [('VP', False)])
        self.assertEqual([(d['position'], d['passed']) for d in b['decided']], [('President', True)])
        # Only the open position needs (and takes) a choice this round.
        self.assertEqual(self.cast(self.access, {'choices': {vp: 'approve'}}).status_code, 200)
        self.assertEqual(SlatingBallot.objects.filter(vote_type='individual', voting_attempt=2).count(), 1)
        self.assertEqual(self.cast(self.access, {'choices': {vp: 'approve'}}).json()['error'], 'already_voted')
