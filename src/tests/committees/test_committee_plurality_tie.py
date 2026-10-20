"""
v3.51.1 — a tied committee plurality vote does not pass.

Four committee paths decided a plurality result with `passed = max_votes > 0`,
so any tie with at least one vote was recorded as PASSED (status 'passed'):

  * `tasks.auto_open_close_committee_votes` (the scheduled close),
  * `committee_vote` → End vote,
  * `committee_vote` → Recalculate,
  * `recalculate_committee_vote`.

Meanwhile `CommitteeLegislation.set_passed()`, the chapter-side task and
chapter `end_vote` (fixed in v3.14.0: "a tie used to pass here") all require a
single clear winner, and the committee result page itself shows the tie and
offers a runoff. Now every committee path uses `plurality_passed()`.

Run with: python manage.py test src.tests.committees.test_committee_plurality_tie
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Committee, CommitteeLegislation, CommitteeVote, ParliamentUser
from src.vote_rules import plurality_passed


def make_user(uid, member_type='Member'):
    return ParliamentUser.objects.create(
        user_id=uid, username=uid.lower(), name=f'User {uid}', member_type=member_type,
        member_status='Active', email=f'{uid.lower()}@example.com')


class PluralityPassedTests(TestCase):
    def test_rule(self):
        self.assertTrue(plurality_passed({'A': 2, 'B': 1, 'total': 3}))
        self.assertFalse(plurality_passed({'A': 1, 'B': 1, 'total': 2}))
        self.assertFalse(plurality_passed({'A': 0, 'B': 0, 'total': 0}))
        self.assertFalse(plurality_passed({'total': 0}))
        self.assertFalse(plurality_passed({'A': 2, 'B': 2, 'C': 1, 'total': 5}))


class _TieBase(TestCase):
    def setUp(self):
        self.committee = Committee.objects.create(name='Tie Committee', code='TIEC', is_active=True)
        self.chair = make_user('TIE-CH', 'Chair')
        self.committee.chairs.add(self.chair)
        self.voters = [make_user(f'TIE-V{i}') for i in range(2)]

    def make_leg(self, choices, **kwargs):
        defaults = dict(
            committee=self.committee, title='Pick one', description='d', posted_by=self.chair,
            available_at=timezone.now() - timedelta(hours=1), vote_mode='plurality',
            plurality_options=['A', 'B'],
        )
        defaults.update(kwargs)
        leg = CommitteeLegislation.objects.create(**defaults)
        for voter, choice in zip(self.voters, choices):
            CommitteeVote.objects.create(legislation=leg, user=voter, vote_choice=choice)
        return leg


class TaskTests(_TieBase):
    def test_tie_does_not_pass(self):
        from src.tasks import auto_open_close_committee_votes
        leg = self.make_leg(['A', 'B'], voting_ends_at=timezone.now() - timedelta(minutes=1))
        auto_open_close_committee_votes()
        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        self.assertFalse(leg.passed)
        self.assertNotEqual(leg.status, 'passed')

    def test_clear_winner_passes(self):
        from src.tasks import auto_open_close_committee_votes
        leg = self.make_leg(['A', 'A'], voting_ends_at=timezone.now() - timedelta(minutes=1))
        auto_open_close_committee_votes()
        leg.refresh_from_db()
        self.assertTrue(leg.passed)
        self.assertEqual(leg.status, 'passed')


class ViewTests(_TieBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.chair)

    def test_end_vote_tie_does_not_pass(self):
        leg = self.make_leg(['A', 'B'])
        self.client.post(reverse('vote', args=[self.committee.code]),
                         {'end_vote': '1', 'legislation_id': leg.pk})
        leg.refresh_from_db()
        self.assertTrue(leg.voting_closed)
        self.assertFalse(leg.passed)

    def test_recalculate_in_vote_page_tie_does_not_pass(self):
        leg = self.make_leg(['A', 'B'], voting_closed=True, passed=True, status='passed')
        self.client.post(reverse('vote', args=[self.committee.code]),
                         {'recalculate_result': '1', 'legislation_id': leg.pk})
        leg.refresh_from_db()
        self.assertFalse(leg.passed)

    def test_recalculate_view_tie_does_not_pass(self):
        leg = self.make_leg(['A', 'B'], voting_closed=True, passed=True, status='passed')
        self.client.post(reverse('recalculate_committee_vote', args=[leg.pk]))
        leg.refresh_from_db()
        self.assertFalse(leg.passed)

    def test_end_vote_clear_winner_still_passes(self):
        leg = self.make_leg(['B', 'B'])
        self.client.post(reverse('vote', args=[self.committee.code]),
                         {'end_vote': '1', 'legislation_id': leg.pk})
        leg.refresh_from_db()
        self.assertTrue(leg.passed)


class ChapterEndVoteNoBallotsTests(TestCase):
    """
    v3.51.1 — chapter `end_vote` passed a plurality vote with no ballots: its
    tie check also needed `max > 0`, so 0-0 counted as "no tie" and the first
    option won. Since v3.51.0 `assign_appointment` trusts `passed`, so that
    "winner" would have been assignable.
    """

    def setUp(self):
        from src.models import Legislation
        self.uploader = make_user('EV-UP', 'Chair')
        self.leg = Legislation.objects.create(
            title='Appoint someone', description='d', document='x.pdf', posted_by=self.uploader,
            available_at=timezone.now(), vote_mode='plurality', plurality_options=['A', 'B'])
        self.client.force_login(self.uploader)

    def test_no_ballots_does_not_pass(self):
        self.client.post(reverse('end_vote', args=[self.leg.pk]))
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.voting_closed)
        self.assertFalse(self.leg.passed)
        self.assertEqual(self.leg.status, 'failed')

    def test_clear_winner_passes(self):
        from src.models import Vote
        Vote.objects.create(legislation=self.leg, user=make_user('EV-V1'), vote_choice='B')
        self.client.post(reverse('end_vote', args=[self.leg.pk]))
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.passed)
