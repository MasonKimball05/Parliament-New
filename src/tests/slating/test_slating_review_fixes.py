"""
v4.0.0 — fixes from the slating review (10-26-26).

* Locked periods: site admins no longer read interview notes, schedule
  interviews or see live tallies through views that checked `is_admin`.
* Votes: a slate ballot and its vote are one locked write (a double submit
  recorded two), rejected-position ids are checked, individual votes too.
* Individual tallies: one grouped query, same numbers.
* Apply: junk / other-period position ids are dropped, not a 500.

    python manage.py test src.tests.slating.test_slating_review_fixes
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from src.models import (
    Committee, ParliamentUser, Slate, SlateCandidate, SlatingApplication, SlatingAttendance,
    SlatingBallot, SlatingInterview, SlatingPeriod, SlatingPosition, SlatingVote,
)
from src.view.slating.tallies import individual_tallies

PW = 'slating-review-pw-1!'


def member(uid, **kw):
    u = ParliamentUser.objects.create(user_id=uid, username=uid, name=f'Member {uid}',
                                      member_type=kw.pop('member_type', 'Member'),
                                      member_status='Active', **kw)
    u.set_password(PW)
    u.save()
    return u


class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.manager = member('sr-mgr')
        self.admin = member('sr-admin', is_admin=True)
        self.voter = member('sr-voter')
        self.committee = Committee.objects.create(name='Slating', code='SLATE', is_active=True)
        self.committee_member = member('sr-cm')
        self.committee.members.add(self.committee_member)
        # A manager makes the period LOCKED: site admin alone is not enough.
        self.period = SlatingPeriod.objects.create(
            name='Fall', status='voting_open', slating_manager=self.manager,
            slating_committee=self.committee, current_voting_attempt=1,
            required_approval_percentage=66)
        self.pres = SlatingPosition.objects.create(period=self.period, title='President', code='P')
        self.vp = SlatingPosition.objects.create(period=self.period, title='VP', code='V')
        self.app = SlatingApplication.objects.create(period=self.period, applicant=self.voter,
                                                     status='submitted')
        self.slate = Slate.objects.create(period=self.period, is_approved=True)
        self.c_pres = SlateCandidate.objects.create(slate=self.slate, position=self.pres,
                                                    application=self.app)
        self.c_vp = SlateCandidate.objects.create(slate=self.slate, position=self.vp,
                                                  write_in_member=self.manager)
        SlatingAttendance.objects.create(period=self.period, member=self.voter)


class LockedPeriodTests(_Base):

    def test_site_admin_cannot_read_interview_notes(self):
        interview = SlatingInterview.objects.create(application=self.app, notes='CONFIDENTIAL')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('slating_complete_interview', args=[interview.id]),
                            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertNotContains(r, 'CONFIDENTIAL', status_code=302)
        self.client.force_login(self.committee_member)
        r = self.client.get(reverse('slating_complete_interview', args=[interview.id]),
                            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)

    def test_manager_can_schedule_and_site_admin_cannot(self):
        url = reverse('slating_schedule_interview', args=[self.app.id])
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_live_tallies_hidden_from_site_admin(self):
        SlatingVote.objects.create(period=self.period, slate=self.slate, voting_attempt=1,
                                   vote_choice='approve', vote_hash='h1')
        url = reverse('slating_api_period_status', args=[self.period.id])
        self.client.force_login(self.admin)
        body = self.client.get(url).json()
        self.assertNotIn('approve', body['voting'])
        self.assertNotIn('stats', body)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).json()['voting']['approve'], 1)


class SlateVoteTests(_Base):

    def vote(self, **data):
        self.client.force_login(self.voter)
        data.setdefault('password', PW)
        return self.client.post(reverse('slating_vote', args=[self.period.id]), data)

    def test_second_submit_records_nothing(self):
        self.vote(vote_choice='approve')
        self.vote(vote_choice='reject', rejected_positions=[str(self.pres.id)])
        self.assertEqual(SlatingBallot.objects.filter(voter=self.voter).count(), 1)
        self.assertEqual(SlatingVote.objects.count(), 1)

    def test_rejected_positions_are_checked(self):
        other = SlatingPosition.objects.create(
            period=SlatingPeriod.objects.create(name='Other'), title='X', code='X')
        r = self.vote(vote_choice='reject',
                      rejected_positions=['junk', str(other.id), str(self.vp.id)])
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SlatingVote.objects.get().rejected_positions, [self.vp.id])

    def test_only_foreign_rejections_is_refused(self):
        self.vote(vote_choice='reject', rejected_positions=['9999'])
        self.assertFalse(SlatingVote.objects.exists())
        self.assertFalse(SlatingBallot.objects.exists())


class IndividualTallyTests(_Base):

    def test_matches_per_candidate_counts(self):
        for i, (c, choice) in enumerate([(self.c_pres, 'approve'), (self.c_pres, 'approve'),
                                         (self.c_pres, 'reject'), (self.c_vp, 'abstain')]):
            SlatingVote.objects.create(period=self.period, slate_candidate=c, vote_choice=choice,
                                       vote_hash=f'v{i}')
        t = individual_tallies([self.c_pres, self.c_vp])
        self.assertEqual({k: t[self.c_pres.id][k] for k in ('approve', 'reject', 'abstain')},
                         {'approve': 2, 'reject': 1, 'abstain': 0})
        self.assertEqual({k: t[self.c_vp.id][k] for k in ('approve', 'reject', 'abstain')},
                         {'approve': 0, 'reject': 0, 'abstain': 1})

    def test_individual_double_submit_is_harmless(self):
        self.period.vote_type = 'individual'
        self.period.save()
        self.client.force_login(self.voter)
        url = reverse('slating_vote_individual', args=[self.period.id])
        data = {f'vote_{self.pres.id}': 'approve', f'vote_{self.vp.id}': 'approve', 'password': PW}
        self.client.post(url, data)
        self.client.post(url, data)
        self.assertEqual(SlatingBallot.objects.filter(voter=self.voter).count(), 2)
        self.assertEqual(SlatingVote.objects.count(), 2)


class ApplyPositionIdsTests(TestCase):

    def test_junk_and_foreign_ids_are_dropped(self):
        applicant = member('sr-applicant')
        period = SlatingPeriod.objects.create(name='Spring', status='nominations_open')
        mine = SlatingPosition.objects.create(period=period, title='President', code='P')
        theirs = SlatingPosition.objects.create(
            period=SlatingPeriod.objects.create(name='Other'), title='X', code='X')
        self.client.force_login(applicant)
        r = self.client.post(reverse('slating_apply', args=[period.id]), {
            'action': 'submit',
            'positions_first_choice': ['nope', str(theirs.id), str(mine.id)],
            'positions_second_choice': [str(mine.id)],
        })
        self.assertEqual(r.status_code, 302)
        app = SlatingApplication.objects.get(applicant=applicant)
        self.assertEqual(app.position_preferences['first_choice'], [mine.id])
        self.assertEqual(app.position_preferences['second_choice'], [])
