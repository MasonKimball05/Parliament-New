"""
The chapter's slating rounds (10-29-26, src/slating_rounds.py): Bylaws Art. II
§3 and the run-off resolution, driven through the chair's real views.

    python manage.py test src.tests.slating.test_rounds
"""
from django.urls import reverse

from src.models import SlateCandidate, SlatingAttendance, SlatingNominee, SlatingPosition
from src.slating_ballots import cast_individual, cast_slate, individual_ballot
from src.slating_rounds import position_states, winners
from src.tests.slating.test_slating_review_fixes import _Base, member


class RoundsTests(_Base):
    """
    The fixture's slate: President (primary only) and VP (write-in). Here VP
    gets a run-off candidate and Treasurer is left for floor nominations.
    Round 1 was opened by the fixture (attempt 1, voting open).
    """

    def setUp(self):
        super().setUp()
        self.vp_runoff_member = member('rr-vp2')
        SlateCandidate.objects.create(slate=self.slate, position=self.vp, is_runoff=True,
                                      write_in_member=self.vp_runoff_member)
        self.treas = SlatingPosition.objects.create(period=self.period, title='Treasurer', code='T',
                                                    display_order=5, open_nominations=True)
        self.voters = [self.voter] + [member(f'rr-v{i}') for i in range(4)]
        for v in self.voters[1:]:
            SlatingAttendance.objects.create(period=self.period, member=v)
        self.client.force_login(self.manager)

    # ── helpers ──

    def state(self, position):
        return next(s for s in position_states(self.period) if s.position == position)

    def open(self, vote_type, positions=(), **extra):
        r = self.client.post(reverse('slating_change_status', args=[self.period.id]),
                             {'status': 'voting_open', 'vote_type': vote_type,
                              'ballot_positions': [p.id for p in positions], **extra})
        self.period.refresh_from_db()
        return r

    def close(self):
        self.client.post(reverse('slating_close_voting', args=[self.period.id]))
        self.period.refresh_from_db()

    def slate_votes(self, *choices):
        cands = list(self.slate.candidates.all())
        for voter, choice in zip(self.voters, choices):
            cast_slate(voter, self.period, self.slate, cands, choice,
                       [self.pres.id] if choice == 'reject' else [])

    def position_votes(self, picks):
        """picks: one {position: choice} per voter."""
        for voter, chosen in zip(self.voters, picks):
            decided, ballot, voted, config = individual_ballot(self.period, self.slate, voter)
            cast_individual(voter, self.period, ballot, voted, config,
                            lambda s, chosen=chosen: chosen.get(s.position))

    def key(self, who):
        if isinstance(who, SlatingNominee):
            return f'n{who.id}'
        return f'c{SlateCandidate.objects.get(slate=self.slate, write_in_member=who).id}' \
            if not hasattr(who, 'position') else f'c{who.id}'

    # ── tests ──

    def test_slate_passes_then_runoff_then_nominations(self):
        self.slate_votes('approve', 'approve', 'approve', 'reject', 'abstain')   # 75% of counted
        self.close()
        self.assertEqual(self.period.status, 'deliberation')
        self.assertEqual(self.state(self.pres).winner.name, self.voter.name)        # elected by the slate
        self.assertFalse(self.state(self.vp).decided)                               # run-off still to go
        self.assertFalse(self.state(self.treas).decided)

        # Run-off: pick one of the two, no abstain.
        self.open('individual', [self.vp], abstain_set='1')
        self.assertEqual(self.period.current_voting_attempt, 2)
        primary, runoff = self.state(self.vp).primary, self.state(self.vp).runoff
        self.position_votes([{self.vp: f'c{runoff.id}'}] * 4 + [{self.vp: f'c{primary.id}'}])   # 80%
        self.close()
        self.assertEqual(self.state(self.vp).winner.name, self.vp_runoff_member.name)

        # Floor nominations: only accepted nominees are on the ballot.
        a = SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[1], accepted=True)
        b = SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[2], accepted=True)
        SlatingNominee.objects.create(period=self.period, position=self.treas, member=self.voters[3])  # hasn't accepted
        self.open('individual', [self.treas], abstain_set='1', abstain='on')
        decided, ballot, voted, config = individual_ballot(self.period, self.slate, self.voter)
        self.assertEqual([s.position for s in ballot], [self.treas])
        self.position_votes([{self.treas: f'n{a.id}'}] * 2 + [{self.treas: f'n{b.id}'}] * 2 + [{self.treas: 'abstain'}])
        self.close()
        self.assertFalse(self.state(self.treas).decided)                    # a tie elects no one

        # Top two, no abstain, final ballot at 51%.
        self.open('individual', [self.treas], threshold='51', abstain_set='1',
                  **{f'nominees_{self.treas.id}': [a.id, b.id]})
        self.position_votes([{self.treas: f'n{a.id}'}] * 3 + [{self.treas: f'n{b.id}'}] * 2)   # 60%
        self.close()
        self.assertEqual(self.state(self.treas).winner.name, self.voters[1].name)
        self.assertEqual(self.period.status, 'voting_closed')               # everything decided
        self.assertEqual(winners(self.period), {self.pres.id: self.voter, self.vp.id: self.vp_runoff_member,
                                                self.treas.id: self.voters[1]})

    def test_first_ballot_is_the_full_slate(self):
        # The fixture's period is already in a round with no SlatingRound rows; simulate a fresh one.
        self.period.status, self.period.current_voting_attempt = 'deliberation', 0
        self.period.save()
        self.open('individual', [self.pres])
        self.assertEqual(self.period.status, 'deliberation')               # refused
        self.open('slate')
        self.assertEqual((self.period.status, self.period.current_voting_attempt), ('voting_open', 1))

    def test_failed_slate_goes_office_by_office(self):
        self.slate_votes('reject', 'reject', 'reject', 'approve', 'approve')   # 40%
        self.close()
        self.assertFalse(any(s.decided for s in position_states(self.period)))
        # The committee may present the slate again after a failure.
        self.open('slate')
        self.assertEqual((self.period.status, self.period.current_voting_attempt), ('voting_open', 2))
        self.close()                                                        # no votes: fails again
        self.open('individual')                                             # nothing chosen
        self.assertEqual(self.period.status, 'deliberation')
        self.open('individual', [self.pres])
        self.position_votes([{self.pres: 'reject'}] * 3 + [{self.pres: 'approve'}] * 2)
        self.close()
        self.assertTrue(self.state(self.pres).failed_last)
        self.assertFalse(self.state(self.pres).decided)
        # The committee renominates (here: the same nominee) and the office is voted again.
        self.open('individual', [self.pres])
        self.position_votes([{self.pres: 'approve'}] * 5)
        self.close()
        self.assertTrue(self.state(self.pres).decided)
        # With only the run-off and floor nominations left, there's no slate to re-run.
        self.open('slate')
        self.assertEqual(self.period.status, 'deliberation')

    def test_chapter_decision_and_unfilled(self):
        self.slate_votes(*['approve'] * 5)
        self.close()
        runoff = self.state(self.vp).runoff
        r = self.client.post(reverse('slating_position_action', args=[self.period.id]),
                             {'action': 'decide', 'position_id': self.vp.id, 'choice': f'c{runoff.id}', 'reason': ''})
        self.assertFalse(self.state(self.vp).decided)                       # a reason is required
        self.client.post(reverse('slating_position_action', args=[self.period.id]),
                         {'action': 'decide', 'position_id': self.vp.id, 'choice': f'c{runoff.id}',
                          'reason': 'Tied twice; the chapter chose by a show of hands'})
        self.assertEqual(self.state(self.vp).winner.name, self.vp_runoff_member.name)
        self.client.post(reverse('slating_position_action', args=[self.period.id]),
                         {'action': 'decide', 'position_id': self.treas.id, 'choice': 'unfilled',
                          'reason': 'No one reached 51% after two ballots'})
        self.assertTrue(self.state(self.treas).unfilled)
        self.assertEqual(r.status_code, 302)

    def test_nominee_management(self):
        url = reverse('slating_position_action', args=[self.period.id])
        self.client.post(url, {'action': 'add_nominee', 'position_id': self.treas.id,
                               'member_id': self.voters[1].user_id})
        n = SlatingNominee.objects.get()
        self.assertFalse(n.accepted)
        self.client.post(url, {'action': 'accept_nominee', 'nominee_id': n.id})
        n.refresh_from_db()
        self.assertTrue(n.accepted)
        self.client.post(url, {'action': 'nominations_off', 'position_id': self.treas.id})
        self.treas.refresh_from_db()
        self.assertFalse(self.treas.open_nominations)
        self.client.post(url, {'action': 'add_nominee', 'position_id': self.treas.id,
                               'member_id': self.voters[2].user_id})
        self.assertEqual(SlatingNominee.objects.count(), 1)                 # closed: refused
        self.client.post(url, {'action': 'remove_nominee', 'nominee_id': n.id})
        self.assertFalse(SlatingNominee.objects.exists())

    def test_pages_render(self):
        self.slate_votes(*['approve'] * 5)
        self.close()
        for name in ('slating_period_setup', 'slating_results'):
            r = self.client.get(reverse(name, args=[self.period.id]))
            self.assertEqual(r.status_code, 200, name)
        self.assertContains(self.client.get(reverse('slating_period_setup', args=[self.period.id])), 'Next round')
