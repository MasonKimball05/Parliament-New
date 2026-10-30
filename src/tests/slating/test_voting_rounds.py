"""
v3.58.0 — slating voting history, paper ballots, hidden results, no IPs,
pledge rules, and the destroy-notes button (Mason, 10-26-26).

    python manage.py test src.tests.slating.test_voting_rounds
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from src.models import (
    CommitteeDocument, SlateCandidate, SlatingActivity, SlatingApplication, SlatingBallot,
    SlatingInterview, SlatingPaperBallots, SlatingRound, SlatingVote,
)
from src.tests.slating.test_slating_review_fixes import PW, _Base, member
from src.view.slating.results import _save_results_to_documents


class _Voting(_Base):

    def setUp(self):
        super().setUp()
        self.voter2 = member('vr-voter2')
        from src.models import SlatingAttendance
        SlatingAttendance.objects.create(period=self.period, member=self.voter2)

    def vote(self, user, choice, **extra):
        self.client.force_login(user)
        data = {'vote_choice': choice, 'password': PW, **extra}
        return self.client.post(reverse('slating_vote', args=[self.period.id]), data)

    def as_manager(self):
        self.client.force_login(self.manager)

    def close(self):
        self.as_manager()
        return self.client.post(reverse('slating_close_voting', args=[self.period.id]))

    def set_status(self, status, **extra):
        self.as_manager()
        return self.client.post(reverse('slating_change_status', args=[self.period.id]),
                                {'status': status, **extra})

    def refresh(self):
        self.period.refresh_from_db()


class RoundHistoryTests(_Voting):

    def test_failed_round_is_kept_and_reopening_starts_a_new_round(self):
        self.vote(self.voter, 'reject', rejected_positions=[str(self.pres.id)])
        self.close()
        self.refresh()
        self.assertEqual(self.period.status, 'deliberation')
        r1 = SlatingRound.objects.get(period=self.period, attempt=1)
        self.assertEqual((r1.status, r1.results['slate']['reject']), ('failed', 1))

        self.set_status('voting_open', vote_type='slate')
        self.refresh()
        self.assertEqual(self.period.current_voting_attempt, 2)
        self.vote(self.voter, 'approve')       # can vote again in the new round
        self.assertEqual(SlatingBallot.objects.filter(voter=self.voter).count(), 2)
        self.close()
        self.refresh()
        self.assertEqual(self.period.status, 'voting_closed')
        self.assertEqual(SlatingVote.objects.count(), 2)   # round 1's vote kept

        doc = _save_results_to_documents(self.period, self.manager)
        text = doc.document.read().decode()
        self.assertIn('VOTING HISTORY', text)
        self.assertIn('Round 1 — full slate — FAILED', text)
        self.assertIn('Round 2 — full slate — PASSED', text)
        self.assertIn('Total Votes Cast: 1', text)   # final round only, not both

    def test_pause_resumes_the_same_round(self):
        self.vote(self.voter, 'approve')
        self.set_status('deliberation')
        self.set_status('voting_open', vote_type='slate')
        self.refresh()
        self.assertEqual(self.period.current_voting_attempt, 1)
        r = self.vote(self.voter, 'approve')
        self.assertEqual(SlatingBallot.objects.filter(voter=self.voter).count(), 1)

    def test_void_keeps_the_round_and_reopening_is_a_fresh_round(self):
        self.vote(self.voter, 'approve')
        self.set_status('deliberation')
        self.as_manager()
        self.client.post(reverse('slating_reset_votes', args=[self.period.id]))
        self.assertEqual(SlatingVote.objects.count(), 1)          # kept
        self.assertEqual(SlatingRound.objects.get(attempt=1).status, 'voided')
        self.set_status('voting_open', vote_type='slate')
        self.refresh()
        self.assertEqual(self.period.current_voting_attempt, 2)
        self.vote(self.voter, 'approve')
        self.assertEqual(SlatingBallot.objects.filter(voter=self.voter, voting_attempt=2).count(), 1)

    def test_individual_revote_keeps_the_failed_round(self):
        self.period.vote_type = 'individual'
        self.period.save()
        self.client.force_login(self.voter)
        url = reverse('slating_vote_individual', args=[self.period.id])
        self.client.post(url, {f'vote_{self.pres.id}': 'approve', f'vote_{self.vp.id}': 'reject',
                               'password': PW})
        self.close()
        self.c_vp.refresh_from_db()
        self.assertIs(self.c_vp.individual_passed, False)
        # 10-29-26: a position round names its positions (src/slating_rounds.py).
        self.set_status('voting_open', vote_type='individual')
        self.refresh()
        self.assertEqual(self.period.current_voting_attempt, 1)          # refused: none chosen
        self.set_status('voting_open', vote_type='individual', ballot_positions=[self.vp.id])
        self.refresh()
        self.assertEqual(self.period.current_voting_attempt, 2)
        self.c_vp.refresh_from_db()
        self.assertIsNone(self.c_vp.individual_passed)
        self.assertEqual(SlatingVote.objects.filter(slate_candidate=self.c_vp).count(), 1)  # kept
        self.client.force_login(self.voter)
        self.client.post(url, {f'vote_{self.vp.id}': 'approve', 'password': PW})
        self.close()
        self.c_vp.refresh_from_db()
        self.assertIs(self.c_vp.individual_passed, True)   # round 2 only: 1 approve
        r1 = SlatingRound.objects.get(attempt=1)
        self.assertEqual([p['position'] for p in r1.results['positions']], ['President', 'VP'])

    def test_replacing_a_candidate_records_their_votes(self):
        self.period.vote_type = 'individual'
        self.period.save()
        self.client.force_login(self.voter)
        self.client.post(reverse('slating_vote_individual', args=[self.period.id]),
                         {f'vote_{self.pres.id}': 'reject', f'vote_{self.vp.id}': 'approve',
                          'password': PW})
        self.as_manager()
        self.client.post(reverse('slating_edit_approved_slate', args=[self.period.id]),
                         {'position_id': self.pres.id, 'candidate_type': 'write_in',
                          'member_id': self.voter2.user_id})
        events = SlatingRound.objects.get(attempt=1).events
        self.assertEqual(events[0]['event'], 'candidate_replaced')
        self.assertEqual(events[0]['reject'], 1)


class PaperBallotTests(_Voting):

    def test_paper_ballots_count_at_close_and_show_separately(self):
        self.vote(self.voter, 'reject', rejected_positions=[str(self.pres.id)])
        self.as_manager()
        r = self.client.post(reverse('slating_paper_ballots', args=[self.period.id]),
                             {'approve': '3', 'reject': '0', 'abstain': '1', 'note': 'tech issues'})
        self.assertRedirects(r, reverse('slating_period_setup', args=[self.period.id]),
                             fetch_redirect_response=False)
        paper = SlatingPaperBallots.objects.get()
        self.assertEqual((paper.ballots, paper.approve), (4, 3))
        self.close()
        self.refresh()
        self.assertEqual(self.period.status, 'voting_closed')   # 3 of 4 counted = 75% ≥ 66%
        rnd = SlatingRound.objects.get(attempt=1)
        self.assertEqual(rnd.results['slate']['paper']['approve'], 3)
        text = _save_results_to_documents(self.period, self.manager).document.read().decode()
        self.assertIn('includes paper: Approve 3, Reject 0, Abstain 1; 4 paper ballot(s)', text)

    def test_resaving_replaces_and_closed_rounds_refuse(self):
        self.as_manager()
        url = reverse('slating_paper_ballots', args=[self.period.id])
        self.client.post(url, {'approve': '2'})
        self.client.post(url, {'approve': '1'})
        self.assertEqual(SlatingPaperBallots.objects.get().approve, 1)
        self.close()
        self.client.post(url, {'approve': '9'})
        self.assertEqual(SlatingPaperBallots.objects.get().approve, 1)

    def test_individual_counts_cant_exceed_ballots(self):
        self.period.vote_type = 'individual'
        self.period.save()
        self.as_manager()
        self.client.post(reverse('slating_paper_ballots', args=[self.period.id]),
                         {'ballots': '2', f'approve_c{self.c_pres.id}': '3'})
        self.assertFalse(SlatingPaperBallots.objects.exists())

    def test_not_for_non_managers(self):
        self.client.force_login(self.voter)
        self.client.post(reverse('slating_paper_ballots', args=[self.period.id]), {'approve': '50'})
        self.assertFalse(SlatingPaperBallots.objects.exists())


class HiddenResultsTests(_Voting):

    def test_voter_sees_no_tally_until_published(self):
        self.vote(self.voter, 'approve')
        r = self.client.get(reverse('slating_vote', args=[self.period.id]))
        self.assertContains(r, 'Results stay private until the slating committee announces them')
        self.assertIsNone(r.context['vote_tally'])
        r = self.client.get(reverse('slating_results', args=[self.period.id]))
        self.assertEqual(r.status_code, 302)
        body = self.client.get(reverse('slating_api_period_status', args=[self.period.id])).json()
        self.assertNotIn('approve', body.get('voting', {}))


class NoIpTests(_Voting):

    def test_slating_activity_has_no_ip(self):
        self.vote(self.voter, 'approve', REMOTE_ADDR='203.0.113.9')
        self.close()
        self.assertTrue(SlatingActivity.objects.exists())
        self.assertFalse(SlatingActivity.objects.exclude(ip_address=None).exists())


class PledgeTests(_Base):

    def test_pledge_can_apply_but_not_be_interviewed(self):
        pledge = member('P-SLATE1', member_type='Pledge')
        self.client.force_login(pledge)
        body = self.client.get(reverse('slating_api_eligibility', args=[self.period.id])).json()
        self.assertTrue(body['eligible'])
        app = SlatingApplication.objects.create(period=self.period, applicant=pledge, status='submitted')
        self.client.force_login(self.manager)
        r = self.client.get(reverse('slating_schedule_interview', args=[app.id]))
        self.assertRedirects(r, reverse('slating_interviews', args=[self.period.id]),
                             fetch_redirect_response=False)
        self.assertFalse(SlatingInterview.objects.filter(application=app).exists())
        page = self.client.get(reverse('slating_interviews', args=[self.period.id]))
        self.assertContains(page, 'Pledge — interview after initiation')

    def test_pledge_on_the_committee_gets_no_access(self):
        pledge = member('P-SLATE2', member_type='Pledge')
        self.committee.members.add(pledge)
        self.client.force_login(pledge)
        r = self.client.get(reverse('slating_applications', args=[self.period.id]))
        self.assertEqual(r.status_code, 302)


class DestroyNotesTests(_Base):

    def test_button_is_there_and_works(self):
        interview = SlatingInterview.objects.create(application=self.app, notes='secret notes')
        self.client.force_login(self.manager)
        page = self.client.get(reverse('slating_interviews', args=[self.period.id]))
        self.assertContains(page, 'Destroy interview notes')
        self.client.post(reverse('slating_destroy_notes', args=[self.period.id]), {'confirm': 'DESTROY'})
        interview.refresh_from_db()
        self.assertTrue(interview.notes_destroyed)
        self.assertNotIn('secret', interview.notes)


class WhoCanApplyTests(_Base):
    """v3.58.0: only active members apply — not inactive, alumni or advisors. Pledges may."""

    def setUp(self):
        super().setUp()
        from src.models import SlatingPeriod, SlatingPosition
        self.open_period = SlatingPeriod.objects.create(name='Spring', status='nominations_open')
        self.position = SlatingPosition.objects.create(period=self.open_period, title='President', code='P')

    def apply(self, user):
        self.client.force_login(user)
        return self.client.post(reverse('slating_apply', args=[self.open_period.id]), {
            'action': 'submit', 'positions_first_choice': [str(self.position.id)]})

    def test_ineligible_members_are_refused_everywhere(self):
        from src.models import ParliamentUser
        cases = [member('wa-inactive'), member('wa-alum'), member('wa-advisor', member_type='Advisor')]
        ParliamentUser.objects.filter(pk=cases[0].pk).update(member_status='Inactive')
        ParliamentUser.objects.filter(pk=cases[1].pk).update(member_status='Alumni')
        for user in cases:
            user.refresh_from_db()
            self.apply(user)
            self.assertFalse(SlatingApplication.objects.filter(applicant=user).exists(), user.user_id)
            body = self.client.get(reverse('slating_api_eligibility', args=[self.open_period.id])).json()
            self.assertFalse(body['eligible'], user.user_id)

    def test_active_members_can_apply(self):
        user = member('wa-active')
        self.apply(user)
        self.assertTrue(SlatingApplication.objects.filter(applicant=user).exists())

    def test_pledges_can_apply_when_the_vpe_allows_the_page(self):
        # Migration 0002 seeds a "Slating — Apply" pledge restriction (blocked
        # for every phase); the VPE opens it from the education dashboard.
        from src.models.education import PledgePageRestriction
        from django.core.cache import cache
        pledge = member('P-WA0001', member_type='Pledge')
        self.assertEqual(self.apply(pledge).status_code, 403)
        PledgePageRestriction.objects.filter(url_name='slating_apply').update(allowed_phases=['all'])
        cache.clear()
        self.apply(pledge)
        self.assertTrue(SlatingApplication.objects.filter(applicant=pledge).exists())

    def test_import_skips_ineligible_members(self):
        from src import slating_import as si
        from src.models import ParliamentUser
        alum = member('wa-alum2', email='alum@test.org')
        ParliamentUser.objects.filter(pk=alum.pk).update(member_status='Alumni')
        member('wa-ok', email='ok@test.org')
        headers, rows = si.parse_csv('Email,Position\nalum@test.org,President\nok@test.org,President\n')
        items = si.plan(self.open_period, headers, rows, {0: 'applicant_email', 1: 'pos_first'})
        self.assertEqual([i['status'] for i in items], ['skip', 'import'])
        self.assertIn('Only active members', items[0]['reason'])
