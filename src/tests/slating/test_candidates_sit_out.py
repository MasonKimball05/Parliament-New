"""
v4.4.5: "Candidates can't vote on their own race" (a slating period setting,
src/slating_ballots.py). Off by default. When on, someone on the slate casts no
slate ballot; on a position vote they skip only their own positions. Nothing is
recorded for them — no ballot and no abstention — so turnout, abstain counts
and pass/fail (approve ÷ approve+reject) only reflect everyone else.

    python manage.py test src.tests.slating.test_candidates_sit_out
"""
from django.urls import reverse

from src.models import SlateCandidate, SlatingBallot, SlatingNominee, SlatingVote
from src.slating_ballots import (CANDIDATES_CANNOT_VOTE, SlatingBallotError, cast_individual, cast_slate,
                                 individual_ballot,
                                 sits_out_slate)
from src.view.slating.tallies import slate_tally
from src.tests.slating.test_rounds import RoundsTests


class CandidatesSitOutTests(RoundsTests):
    """The RoundsTests fixture: self.voter is the President candidate on the slate."""

    def turn_on(self):
        self.period.extra_settings = {**(self.period.extra_settings or {}), CANDIDATES_CANNOT_VOTE: True}
        self.period.save(update_fields=['extra_settings'])

    def records(self, user):
        return SlatingBallot.objects.filter(period=self.period, voter=user).count()

    # ── off by default ──

    def test_off_by_default_candidates_vote(self):
        self.assertFalse(sits_out_slate(self.period, self.slate, self.voter))
        cast_slate(self.voter, self.period, self.slate, list(self.slate.candidates.all()), 'approve', [])
        self.assertEqual(self.records(self.voter), 1)

    # ── whole-slate vote ──

    def test_slate_candidate_cannot_cast_and_nothing_is_recorded(self):
        self.turn_on()
        votes_before = SlatingVote.objects.filter(period=self.period).count()
        with self.assertRaises(SlatingBallotError) as e:
            cast_slate(self.voter, self.period, self.slate, list(self.slate.candidates.all()), 'abstain', [])
        self.assertEqual(e.exception.code, 'candidate_cannot_vote')
        self.assertEqual(self.records(self.voter), 0)                                   # no ballot
        self.assertEqual(SlatingVote.objects.filter(period=self.period).count(), votes_before)  # no abstain

    def test_write_in_and_runoff_members_also_sit_out(self):
        self.turn_on()
        self.assertTrue(sits_out_slate(self.period, self.slate, self.vp_runoff_member))   # run-off write-in

    def test_others_vote_and_the_result_counts_only_their_ballots(self):
        self.turn_on()
        others = [v for v in self.voters if v != self.voter]
        cands = list(self.slate.candidates.all())
        for v, choice in zip(others, ['approve', 'approve', 'approve', 'reject']):
            cast_slate(v, self.period, self.slate, cands, choice, [self.pres.id] if choice == 'reject' else [])
        t = slate_tally(self.period, self.slate, self.period.current_voting_attempt)
        self.assertEqual((t['approve'], t['reject'], t['abstain']), (3, 1, 0))           # candidate isn't an abstain
        self.assertEqual(t['percentage'], 75.0)

    def test_web_page_shows_the_notice_not_the_form_and_post_is_refused(self):
        self.turn_on()
        self.client.force_login(self.voter)
        page = self.client.get(reverse('slating_vote', args=[self.period.id]))
        self.assertContains(page, "You're on this slate, so you don't vote on it")
        self.assertNotContains(page, 'id="vote_approve"')                                # no ballot form
        r = self.client.post(reverse('slating_vote', args=[self.period.id]),
                             {'vote_choice': 'approve', 'password': 'x'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.records(self.voter), 0)

    # ── position vote (run-offs, floor nominations) ──

    def test_position_vote_skips_only_their_own_position(self):
        self.slate_votes('approve', 'approve', 'approve', 'approve', 'approve')
        self.close()
        self.turn_on()
        nominee = SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[1],
                                                accepted=True)
        SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[2], accepted=True)
        self.open('individual', [self.vp, self.treas])
        # The Treasurer nominee votes on VP but not on Treasurer.
        _, ballot, _, _ = individual_ballot(self.period, self.slate, nominee.member)
        self.assertEqual([s.position for s in ballot], [self.vp])
        # The VP run-off candidate votes on Treasurer but not on VP.
        _, ballot, _, _ = individual_ballot(self.period, self.slate, self.vp_runoff_member)
        self.assertEqual([s.position for s in ballot], [self.treas])
        # Someone running for nothing gets both.
        _, ballot, _, _ = individual_ballot(self.period, self.slate, self.voters[4])
        self.assertEqual({s.position for s in ballot}, {self.vp, self.treas})

    def test_a_forged_choice_for_their_own_position_is_not_recorded(self):
        self.slate_votes('approve', 'approve', 'approve', 'approve', 'approve')
        self.close()
        self.turn_on()
        nominee = SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[1],
                                                accepted=True)
        self.open('individual', [self.treas])
        # voters[1] sends a choice for their own position: it isn't on their ballot, so nothing is cast.
        decided, ballot, voted, config = individual_ballot(self.period, self.slate, self.voters[1])
        self.assertEqual(ballot, [])
        cast_individual(self.voters[1], self.period, ballot, voted, config, lambda s: f'n{nominee.id}')
        self.assertFalse(SlatingBallot.objects.filter(period=self.period, voter=self.voters[1],
                                                      position=self.treas).exists())

    # ── setting it ──

    def test_settings_form_turns_it_on_and_off_and_locks_while_voting_is_open(self):
        url = reverse('slating_period_setup', args=[self.period.id])
        base = {'name': self.period.name, 'academic_term': self.period.academic_term,
                # 11-05-26: fixed once voting has started, so posted as it is.
                'required_approval_percentage': str(self.period.required_approval_percentage),
                'min_gpa_requirement': '2.5', 'gpa_level_2_threshold': '0.2'}
        self.close()                                                   # out of voting_open
        self.client.post(url, {**base, 'candidates_cannot_vote': ['off', 'on']})
        self.period.refresh_from_db()
        self.assertTrue(self.period.extra_settings.get(CANDIDATES_CANNOT_VOTE))
        self.client.post(url, base)                                    # field absent: unchanged
        self.period.refresh_from_db()
        self.assertTrue(self.period.extra_settings.get(CANDIDATES_CANNOT_VOTE))
        self.client.post(url, {**base, 'candidates_cannot_vote': 'off'})
        self.period.refresh_from_db()
        self.assertFalse(self.period.extra_settings.get(CANDIDATES_CANNOT_VOTE))
        self.open('slate')
        self.client.post(url, {**base, 'candidates_cannot_vote': ['off', 'on']})   # locked mid-round
        self.period.refresh_from_db()
        self.assertFalse(self.period.extra_settings.get(CANDIDATES_CANNOT_VOTE))
