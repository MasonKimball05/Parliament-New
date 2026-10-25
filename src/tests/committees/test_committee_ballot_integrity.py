"""
P0 vote integrity — committee ballots.

  * A voting member of committee A could cast on committee B's legislation:
    the ballot path looked the legislation up by id alone (IDOR).
  * Every ballot POST redirected to the URL name 'committee_vote', which does
    not exist (the committee vote page is named 'vote'), so a recorded ballot
    answered with a 500.
  * Single-select accepted any string, ignored `allow_abstain`, and voting
    could happen before `voting_starts_at`.
  * The vote-result page referenced `committee.non_voting_members`, which does
    not exist — a 500 for every viewer who isn't a voting member.

Run with: python manage.py test src.tests.committees.test_committee_ballot_integrity
"""
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Attendance, Committee, CommitteeLegislation, CommitteeVote, ParliamentUser,
)


def make_user(uid, member_type='Member'):
    user = ParliamentUser.objects.create(
        user_id=uid, username=uid.lower(), name=f'User {uid}', member_type=member_type,
        member_status='Active', email=f'{uid.lower()}@example.com')
    user.set_password('testpass')
    user.save()
    return user


class _BallotBase(TestCase):
    def setUp(self):
        cache.clear()  # vote password rate-limit counters
        self.committee_a = Committee.objects.create(name='Committee A', code='CBA', is_active=True)
        self.committee_b = Committee.objects.create(name='Committee B', code='CBB', is_active=True)
        self.chair = make_user('CB-CH', 'Chair')
        self.committee_a.chairs.add(self.chair)
        self.committee_b.chairs.add(self.chair)
        self.voter = make_user('CB-V1')
        self.committee_a.members.add(self.voter)
        self.committee_a.voting_members.add(self.voter)
        Attendance.objects.create(user=self.voter, status='present')
        self.client.force_login(self.voter)

    def make_leg(self, committee=None, **kwargs):
        defaults = dict(
            committee=committee or self.committee_a, title='Motion', description='d',
            posted_by=self.chair, available_at=timezone.now() - timedelta(hours=1),
        )
        defaults.update(kwargs)
        return CommitteeLegislation.objects.create(**defaults)

    def cast(self, leg, code=None, follow=False, **data):
        payload = {'action': 'cast_vote', 'legislation_id': leg.pk, 'password': 'testpass'}
        payload.update(data)
        return self.client.post(reverse('vote', args=[code or self.committee_a.code]),
                                payload, follow=follow)


class CrossCommitteeBallotTests(_BallotBase):
    def test_cannot_vote_on_other_committees_legislation(self):
        leg_b = self.make_leg(committee=self.committee_b)
        resp = self.cast(leg_b, vote_choice='yes')
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(CommitteeVote.objects.filter(legislation=leg_b).exists())

    def test_own_committee_ballot_records_and_redirects(self):
        leg = self.make_leg()
        resp = self.cast(leg, vote_choice='yes')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp['Location'], reverse('vote', args=[self.committee_a.code]))
        self.assertTrue(CommitteeVote.objects.filter(
            user=self.voter, legislation=leg, vote_choice='yes').exists())


class BallotValidationTests(_BallotBase):
    def test_rejects_arbitrary_choice(self):
        leg = self.make_leg()
        self.cast(leg, vote_choice='banana')
        self.assertFalse(CommitteeVote.objects.exists())

    def test_rejects_missing_choice(self):
        leg = self.make_leg()
        self.cast(leg)
        self.assertFalse(CommitteeVote.objects.exists())

    def test_rejects_abstain_when_not_allowed(self):
        leg = self.make_leg(allow_abstain=False)
        self.cast(leg, vote_choice='abstain')
        self.assertFalse(CommitteeVote.objects.exists())

    def test_abstain_allowed_by_default(self):
        leg = self.make_leg()
        self.cast(leg, vote_choice='abstain')
        self.assertTrue(CommitteeVote.objects.filter(vote_choice='abstain').exists())

    def test_rejects_before_voting_starts(self):
        leg = self.make_leg(voting_starts_at=timezone.now() + timedelta(hours=1))
        self.cast(leg, vote_choice='yes')
        self.assertFalse(CommitteeVote.objects.exists())

    def test_single_plurality_rejects_unknown_option(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B'])
        self.cast(leg, vote_choice='yes')
        self.assertFalse(CommitteeVote.objects.exists())
        self.cast(leg, vote_choice='B')
        self.assertTrue(CommitteeVote.objects.filter(vote_choice='B').exists())

    def test_multi_plurality_rejects_duplicate_choices(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                            plurality_votes_allowed=2)
        resp = self.cast(leg, vote_choices=['A', 'A'])
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(CommitteeVote.objects.exists())

    def test_multi_plurality_is_atomic(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                            plurality_votes_allowed=2)
        real_create = CommitteeVote.objects.create
        calls = {'n': 0}

        def flaky_create(**kw):
            calls['n'] += 1
            if calls['n'] == 2:
                raise RuntimeError('boom')
            return real_create(**kw)

        with mock.patch.object(CommitteeVote.objects, 'create', side_effect=flaky_create):
            with self.assertRaises(RuntimeError):
                self.cast(leg, vote_choices=['A', 'B'])
        self.assertFalse(CommitteeVote.objects.exists())

    def test_multi_plurality_records_each_choice(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                            plurality_votes_allowed=2)
        self.cast(leg, vote_choices=['A', 'C'])
        self.assertEqual(
            sorted(CommitteeVote.objects.filter(user=self.voter).values_list('vote_choice', flat=True)),
            ['A', 'C'])


class DoubleBallotTests(_BallotBase):
    def test_second_ballot_refused(self):
        leg = self.make_leg()
        self.cast(leg, vote_choice='yes')
        resp = self.cast(leg, vote_choice='no', follow=True)
        self.assertEqual(CommitteeVote.objects.filter(user=self.voter, legislation=leg).count(), 1)
        self.assertContains(resp, 'already voted')

    def test_integrity_error_reports_already_voted(self):
        """A ballot that loses a race to the DB constraint gets the normal message, not a 500."""
        leg = self.make_leg()
        with mock.patch.object(CommitteeVote.objects, 'create', side_effect=IntegrityError('dup')):
            resp = self.cast(leg, vote_choice='yes', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already voted')


class VoteResultPageTests(_BallotBase):
    def test_non_voting_member_can_open_result(self):
        leg = self.make_leg(voting_closed=True)
        member = make_user('CB-M2')
        self.committee_a.members.add(member)
        self.client.force_login(member)
        resp = self.client.get(reverse('committee_vote_result', args=[self.committee_a.code, leg.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_outsider_refused_on_open_vote(self):
        leg = self.make_leg()
        outsider = make_user('CB-OUT')
        self.client.force_login(outsider)
        resp = self.client.get(reverse('committee_vote_result', args=[self.committee_a.code, leg.pk]))
        self.assertEqual(resp.status_code, 302)
