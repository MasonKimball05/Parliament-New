"""
Slating fixes before the Saturday election (11-05-26), from the edge-case
simulation's reports (close race, schedule, status jumps, publish, rounds,
committee rule, secret ballot, results, transition).

    python manage.py test src.tests.slating.test_saturday_fixes
"""
import json
import threading
import time
from datetime import timedelta
from unittest import mock

from django.db import connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Committee, ParliamentUser, Role, Slate, SlateCandidate, SlatingActivity, SlatingApplication, SlatingAttendance,
    SlatingBallot, SlatingNominee, SlatingPeriod, SlatingPosition, SlatingRound, SlatingVote,
)
from src.slating_ballots import SlatingBallotError, cast_slate
from src.slating_rounds import RoundError, add_nominee, close_round, decide_manually, position_states, validate_round
from src.tests.mobile.sess_helpers import bearer, make_device
from src.tests.slating.test_slating_review_fixes import PW, _Base, member
from src.view.slating.period_setup import check_and_auto_transition_status

API = '/api/mobile/v1/'


class _Fix(_Base):

    def cands(self):
        return list(self.slate.candidates.all())

    def vote(self, user, choice='approve'):
        SlatingAttendance.objects.get_or_create(period=self.period, member=user)
        cast_slate(user, self.period, self.slate, self.cands(), choice, [self.pres.id] if choice == 'reject' else [])

    def fresh(self):
        return SlatingPeriod.objects.get(pk=self.period.pk)

    def as_manager(self):
        self.client.force_login(self.manager)

    def status(self, status, **extra):
        self.as_manager()
        return self.client.post(reverse('slating_change_status', args=[self.period.id]), {'status': status, **extra})

    def api(self, user, method, path, body=None):
        _, access, _ = make_device(user)
        c = Client()
        if body is None:
            return getattr(c, method)(API + path, **bearer(access))
        return getattr(c, method)(API + path, json.dumps(body), content_type='application/json', **bearer(access))


# ── 1. Close race ────────────────────────────────────────────────────────────

class CloseRaceTests(_Fix):

    def test_ballot_built_before_close_is_refused(self):
        stale = self.fresh()                       # the voter's request loaded the period as open
        close_round(self.fresh(), self.manager)   # the chair closes meanwhile
        with self.assertRaises(SlatingBallotError) as e:
            cast_slate(self.voter, stale, self.slate, self.cands(), 'approve', [])
        self.assertEqual((e.exception.code, str(e.exception)),
                         ('voting_closed', 'Voting closed before your ballot was recorded.'))
        self.assertFalse(SlatingBallot.objects.exists())
        self.assertFalse(SlatingVote.objects.exists())

    def test_ballot_for_an_earlier_round_is_refused(self):
        stale = self.fresh()
        SlatingPeriod.objects.filter(pk=self.period.pk).update(current_voting_attempt=2)
        with self.assertRaises(SlatingBallotError) as e:
            cast_slate(self.voter, stale, self.slate, self.cands(), 'approve', [])
        self.assertEqual(e.exception.code, 'voting_closed')

    def test_a_second_close_is_refused(self):
        stale = self.fresh()
        close_round(self.fresh(), self.manager)
        with self.assertRaises(RoundError) as e:
            close_round(stale, self.manager)
        self.assertEqual(e.exception.code, 'not_open')

    @override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_app_says_voting_closed(self):
        stale = self.fresh()
        with mock.patch('src.mobile.views_slating.SlatingVoteView._ballot', return_value=(stale, self.slate)):
            close_round(self.fresh(), self.manager)
            r = self.api(self.voter, 'post', f'slating/periods/{self.period.id}/vote/',
                         {'choice': 'approve', 'password': PW})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'voting_closed'))


class CloseRaceConcurrencyTests(TransactionTestCase):
    """Real threads: a close holding the period lock makes an in-flight ballot wait, then refuses it."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('row locks need PostgreSQL')
        self.manager = member('cr-mgr')
        self.voter = member('cr-voter')
        self.period = SlatingPeriod.objects.create(name='Race', status='voting_open', current_voting_attempt=1,
                                                   slating_manager=self.manager)
        pos = SlatingPosition.objects.create(period=self.period, title='President', code='P')
        self.slate = Slate.objects.create(period=self.period, is_approved=True)
        SlateCandidate.objects.create(slate=self.slate, position=pos, write_in_member=self.manager)
        SlatingAttendance.objects.create(period=self.period, member=self.voter)

    def test_close_and_vote_at_once(self):
        locked, out = threading.Event(), {}

        def chair():
            try:
                with transaction.atomic():
                    from src.slating_rounds import lock_period
                    p = SlatingPeriod.objects.get(pk=self.period.pk)
                    lock_period(p)
                    locked.set()
                    time.sleep(0.4)    # the ballot arrives while the close holds the lock
                    close_round(p, self.manager)
            finally:
                connection.close()

        stale = SlatingPeriod.objects.get(pk=self.period.pk)
        t = threading.Thread(target=chair)
        t.start()
        locked.wait(5)
        try:
            cast_slate(self.voter, stale, self.slate, list(self.slate.candidates.all()), 'approve', [])
        except SlatingBallotError as e:
            out['code'] = e.code
        t.join(10)
        self.assertEqual(out.get('code'), 'voting_closed')
        rnd = SlatingRound.objects.get(period=self.period, attempt=1)
        # The snapshot and what's stored agree: nothing landed after the count.
        self.assertEqual(rnd.results['slate']['ballots_online'], SlatingBallot.objects.count())
        self.assertEqual(SlatingBallot.objects.count(), 0)


# ── 2. The schedule ──────────────────────────────────────────────────────────

@mock.patch('src.notification_service.notify_all_active_members')
class ScheduleTests(_Fix):

    def round_opened(self, ago):
        rnd, _ = SlatingRound.objects.get_or_create(period=self.period, attempt=1)
        SlatingRound.objects.filter(pk=rnd.pk).update(opened_at=timezone.now() - ago)

    def test_close_time_counts_the_round(self, notify):
        self.round_opened(timedelta(hours=1))
        self.vote(self.voter)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(voting_close_at=timezone.now() - timedelta(seconds=5))
        p = self.fresh()
        self.assertFalse(p.can_vote())             # no more ballots past the close time
        self.assertTrue(check_and_auto_transition_status(p))
        self.assertEqual(p.status, 'voting_closed')
        self.assertEqual(SlatingRound.objects.get(attempt=1).status, 'passed')
        self.c_pres.refresh_from_db()
        self.assertTrue(self.c_pres.individual_passed)            # counted and elected, as Close Voting does
        self.assertFalse(check_and_auto_transition_status(p))     # idempotent

    def test_a_round_opened_by_hand_after_the_close_time_stays_open(self, notify):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(voting_close_at=timezone.now() - timedelta(hours=1))
        self.round_opened(timedelta(minutes=1))
        p = self.fresh()
        self.assertTrue(p.can_vote())
        self.assertFalse(check_and_auto_transition_status(p))
        self.assertEqual(p.status, 'voting_open')

    def test_a_pause_is_never_reopened(self, notify):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            status='deliberation', voting_open_at=timezone.now() - timedelta(minutes=30))
        p = self.fresh()
        self.assertFalse(check_and_auto_transition_status(p))
        self.assertEqual((p.status, p.current_voting_attempt), ('deliberation', 1))

    def test_a_closed_round_is_never_reopened(self, notify):
        self.vote(self.voter, 'reject')
        close_round(self.fresh(), self.manager)            # failed: back to deliberation
        SlatingPeriod.objects.filter(pk=self.period.pk).update(voting_open_at=timezone.now() - timedelta(minutes=5))
        p = self.fresh()
        self.assertEqual(p.status, 'deliberation')
        self.assertFalse(check_and_auto_transition_status(p))
        self.assertEqual(p.current_voting_attempt, 1)

    def test_first_round_opens_as_the_chair_would(self, notify):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            status='nominations_closed', current_voting_attempt=0, voting_open_at=timezone.now() - timedelta(minutes=1))
        p = self.fresh()
        self.assertTrue(check_and_auto_transition_status(p))
        self.assertEqual((p.status, p.current_voting_attempt, p.vote_type), ('voting_open', 1, 'slate'))
        self.assertTrue(SlatingRound.objects.filter(period=p, attempt=1, status='open').exists())
        steps = list(SlatingActivity.objects.filter(period=p, action='status_changed').order_by('pk')
                     .values_list('details', flat=True))
        self.assertEqual(len(steps), 2)   # nominations_closed → deliberation → voting_open, one step at a time
        self.assertIn('nominations_closed to deliberation', steps[0])
        notify.assert_called()            # members told voting is open
        self.assertTrue(p.can_vote())

    def test_publish_time_publishes_with_notifications_and_documents(self, notify):
        from src.models import CommitteeDocument
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            status='voting_closed', results_publish_at=timezone.now() - timedelta(minutes=1))
        p = self.fresh()
        self.assertTrue(check_and_auto_transition_status(p))
        self.assertEqual(p.status, 'results_published')
        self.assertEqual(notify.call_args.args[0], 'slating_results')
        self.assertTrue(CommitteeDocument.objects.filter(title=f'Election Results - {p.name}').exists())

    def test_unpublished_results_stay_unpublished(self, notify):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(
            status='results_published', results_publish_at=timezone.now() - timedelta(minutes=1))
        self.status('voting_closed')   # the results page's Unpublish
        p = self.fresh()
        self.assertEqual(p.status, 'voting_closed')
        self.assertFalse(check_and_auto_transition_status(p))
        self.assertEqual(p.status, 'voting_closed')

    def test_opened_early_by_hand_takes_votes(self, notify):
        # t15: the chair opened a few minutes before the scheduled time.
        SlatingPeriod.objects.filter(pk=self.period.pk).update(voting_open_at=timezone.now() + timedelta(minutes=10))
        self.assertTrue(self.fresh().can_vote())
        self.client.force_login(self.voter)
        self.client.post(reverse('slating_vote', args=[self.period.id]), {'vote_choice': 'approve', 'password': PW})
        self.assertEqual(SlatingBallot.objects.count(), 1)


# ── 3 / 4. Publishing and status changes ────────────────────────────────────

class StatusTests(_Fix):

    def test_publish_refused_while_voting_is_open(self):
        self.as_manager()
        self.client.post(reverse('slating_publish_results', args=[self.period.id]), {'save_to_documents': '1'})
        self.assertEqual(self.fresh().status, 'voting_open')
        r = self.client.get(reverse('slating_results', args=[self.period.id]))
        self.assertFalse(r.context['can_publish_now'])
        self.assertNotContains(r, reverse('slating_publish_results', args=[self.period.id]))

    def test_jumps_are_refused(self):
        for target in ('voting_closed', 'results_published', 'archived', 'setup'):
            self.status(target)
            self.assertEqual(self.fresh().status, 'voting_open', target)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='archived')
        self.status('voting_open', vote_type='slate')
        self.assertEqual(self.fresh().status, 'archived')
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        self.status('voting_open', vote_type='slate')
        self.assertEqual(self.fresh().status, 'results_published')

    def test_the_real_steps_still_work(self):
        self.status('deliberation')                    # pause
        self.assertEqual(self.fresh().status, 'deliberation')
        self.status('voting_open', vote_type='slate')  # resume
        self.assertEqual(self.fresh().status, 'voting_open')
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        self.status('voting_closed')                   # Unpublish
        self.assertEqual(self.fresh().status, 'voting_closed')
        self.status('results_published')
        self.status('archived')
        self.assertEqual(self.fresh().status, 'archived')

    @override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_app_status_is_refused_too(self):
        r = self.api(self.manager, 'post', f'slating/periods/{self.period.id}/setup/status/', {'status': 'voting_closed'})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'not_allowed'))
        self.assertEqual(self.fresh().status, 'voting_open')


# ── 5 / 12. The chapter's decision ──────────────────────────────────────────

class DecisionTests(_Fix):

    def setUp(self):
        super().setUp()
        self.vp2 = member('sf-vp2')
        self.runoff = SlateCandidate.objects.create(slate=self.slate, position=self.vp, is_runoff=True,
                                                    write_in_member=self.vp2)

    def test_settling_the_last_position_closes_voting(self):
        self.vote(self.voter)
        close_round(self.fresh(), self.manager)
        p = self.fresh()
        self.assertEqual(p.status, 'deliberation')    # the run-off is still open
        decide_manually(p, self.manager, self.vp.id, f'c{self.runoff.id}', 'Chosen by a show of hands')
        self.assertEqual(self.fresh().status, 'voting_closed')
        self.as_manager()
        self.client.post(reverse('slating_publish_results', args=[self.period.id]))
        self.assertEqual(self.fresh().status, 'results_published')

    def test_not_while_voting_is_open_or_paused_or_published(self):
        for status in ('voting_open', 'deliberation', 'results_published'):   # deliberation here is a pause
            SlatingPeriod.objects.filter(pk=self.period.pk).update(status=status)
            with self.assertRaises(RoundError, msg=status):
                decide_manually(self.fresh(), self.manager, self.vp.id, f'c{self.runoff.id}', 'reason')
        self.assertFalse(any(s.decided for s in position_states(self.period)))


# ── 6. Transfer ──────────────────────────────────────────────────────────────

class TransferTests(_Fix):

    def test_site_admin_cannot_take_over_a_locked_period(self):
        self.client.force_login(self.admin)
        url = reverse('slating_transfer_admin', args=[self.period.id])
        self.assertEqual(self.client.post(url, {'new_admin': self.admin.user_id, 'reason': 'x'}).status_code, 403)
        self.committee.refresh_from_db()
        self.assertIsNone(self.committee.admin)
        self.as_manager()
        self.assertEqual(self.client.get(url).status_code, 200)


# ── 7. Secret ballot in the Django admin ─────────────────────────────────────

class AdminTests(_Fix):

    def test_vote_and_ballot_lists_cant_be_paired(self):
        from src.admin import admin_site
        for model, key in ((SlatingVote, 'vote_hash'), (SlatingBallot, 'ballot_hash')):
            ma = admin_site._registry[model]
            self.assertEqual(tuple(ma.ordering), (key,))
            self.assertNotIn('voted_at', ma.list_display)
            self.assertEqual(tuple(ma.sortable_by), ())
            self.assertFalse(ma.actions)
            self.assertIsNone(ma.list_display_links)


# ── 8. Results from the closed round ────────────────────────────────────────

class ResultsTests(_Fix):

    def test_changing_the_percentage_doesnt_flip_a_closed_round(self):
        from src.view.slating.results import _save_results_to_documents
        for i, choice in enumerate(['approve', 'approve', 'reject']):   # 66.7% ≥ 66
            self.vote(member(f'sf-r{i}'), choice)
        close_round(self.fresh(), self.manager)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(required_approval_percentage=70)
        self.as_manager()
        r = self.client.get(reverse('slating_results', args=[self.period.id]))
        self.assertTrue(r.context['vote_tally']['passed'])
        self.assertEqual(r.context['vote_tally']['required'], 66)
        text = _save_results_to_documents(self.fresh(), self.manager).document.read().decode()
        self.assertIn('SLATE APPROVED', text)

    def test_percentage_is_51_to_100_and_fixed_once_voting_started(self):
        self.as_manager()
        url = reverse('slating_period_setup', args=[self.period.id])
        base = {'action': 'save', 'name': 'Fall', 'academic_term': 'Fall', 'min_gpa_requirement': '2.5',
                'gpa_level_2_threshold': '0.2'}
        self.client.post(url, {**base, 'required_approval_percentage': '70'})
        self.assertEqual(self.fresh().required_approval_percentage, 66)     # voting has started
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation', current_voting_attempt=0)
        for bad in ('50', '101', 'abc', '60.5'):
            self.client.post(url, {**base, 'required_approval_percentage': bad})
            self.assertEqual(self.fresh().required_approval_percentage, 66, bad)
        self.client.post(url, {**base, 'required_approval_percentage': '51'})
        self.assertEqual(self.fresh().required_approval_percentage, 51)


# ── 9 / 10 / 11 / 15. The slate and who may stand ──────────────────────────

class SlateTests(_Fix):

    def setUp(self):
        super().setUp()
        self.other = member('sf-other')
        self.treas = SlatingPosition.objects.create(period=self.period, title='Treasurer', code='T',
                                                    open_nominations=True)

    def test_no_swap_while_a_round_is_open(self):
        from src.view.slating.slate_builder import ROUND_OPEN_SWAP, SlateError, replace_candidate
        with self.assertRaisesMessage(SlateError, ROUND_OPEN_SWAP):
            replace_candidate(self.period, self.slate, self.manager,
                              {'position_id': self.pres.id, 'candidate_type': 'write_in', 'member_id': self.other.user_id})
        self.assertTrue(SlateCandidate.objects.filter(pk=self.c_pres.pk).exists())
        self.status('deliberation')   # paused: allowed
        replace_candidate(self.fresh(), self.slate, self.manager,
                          {'position_id': self.pres.id, 'candidate_type': 'write_in', 'member_id': self.other.user_id})
        self.assertFalse(SlateCandidate.objects.filter(pk=self.c_pres.pk).exists())

    def test_write_ins_follow_the_candidates_rules(self):
        from src.view.slating.slate_builder import SlateError, add_write_in
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation')
        alum = member('sf-alum')
        ParliamentUser.objects.filter(pk=alum.pk).update(member_status='Alumni')
        for who in (self.committee_member, alum):
            with self.assertRaises(SlateError):
                add_write_in(self.fresh(), self.manager, self.treas.id, who.user_id)
        pledge = member('sf-pledge', member_type='Pledge')   # pledges may run
        add_write_in(self.fresh(), self.manager, self.treas.id, pledge.user_id)
        self.assertTrue(SlateCandidate.objects.filter(position=self.treas, write_in_member=pledge).exists())

    def test_manual_results_refused_once_voting_started(self):
        votes = SlatingVote.objects.create(period=self.period, slate=self.slate, voting_attempt=1,
                                           vote_choice='approve', vote_hash='x1')
        self.as_manager()
        self.client.post(reverse('slating_manual_results', args=[self.period.id]),
                         {'vote_mode': 'none', f'winner_{self.pres.id}': self.other.user_id,
                          f'winner_{self.vp.id}': self.other.user_id, f'winner_{self.treas.id}': self.other.user_id})
        self.assertTrue(SlatingVote.objects.filter(pk=votes.pk).exists())
        self.assertTrue(SlateCandidate.objects.filter(pk=self.c_pres.pk).exists())
        self.assertEqual(self.fresh().status, 'voting_open')

    def test_manual_winners_are_checked(self):
        from src.view.slating.slate_builder import ManualResultsError, record_manual_results
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation', current_voting_attempt=0)
        alum = member('sf-alum2')
        ParliamentUser.objects.filter(pk=alum.pk).update(member_status='Alumni')
        for bad in ('nobody', alum.user_id, self.committee_member.user_id):
            data = {f'winner_{p.id}': self.other.user_id for p in (self.pres, self.vp, self.treas)}
            data[f'winner_{self.pres.id}'] = bad
            with self.assertRaises(ManualResultsError, msg=bad):
                record_manual_results(self.fresh(), self.manager, data)
        self.assertEqual(self.fresh().status, 'deliberation')

    def test_floor_nominees_follow_the_candidates_rules(self):
        alum = member('sf-alum3')
        ParliamentUser.objects.filter(pk=alum.pk).update(member_status='Alumni')
        alum.refresh_from_db()
        for who in (self.committee_member, alum, member('sf-adv', member_type='Advisor')):
            with self.assertRaises(RoundError):
                add_nominee(self.period, self.manager, self.treas.id, who)
        pledge = member('sf-pledge2', member_type='Pledge')
        add_nominee(self.period, self.manager, self.treas.id, pledge)
        self.assertEqual(list(SlatingNominee.objects.values_list('member', flat=True)), [pledge.pk])

    def test_committee_members_cant_apply(self):
        from src.view.slating.apply import apply_refusal
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='nominations_open')
        _, refusal = apply_refusal(self.fresh(), self.committee_member)
        self.assertEqual(refusal, ('committee_member', "Slating committee members can't run in this election."))
        self.client.force_login(self.committee_member)
        body = self.client.get(reverse('slating_api_eligibility', args=[self.period.id])).json()
        self.assertFalse(body['eligible'])

    @override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_app_apply_refuses_committee_members(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='nominations_open')
        r = self.api(self.committee_member, 'get', f'slating/periods/{self.period.id}/apply/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'apply_committee_member'))

    def test_applicants_cant_join_the_committee(self):
        self.as_manager()
        url = reverse('slating_period_setup', args=[self.period.id])
        r = self.client.post(url, {'action': 'add_committee_member', 'member_id': self.voter.user_id}, follow=True)
        self.assertContains(r, f"{self.voter.name} has applied, so they can")
        self.assertFalse(self.committee.members.filter(pk=self.voter.pk).exists())
        # Nor take over as its admin.
        from src.view.slating.transfer_admin import TransferError, transfer_committee_admin
        with self.assertRaises(TransferError):
            transfer_committee_admin(self.period, self.manager, self.voter.user_id, 'running')

    @override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
    def test_app_committee_add_refuses_applicants(self):
        r = self.api(self.manager, 'post', f'slating/periods/{self.period.id}/setup/',
                     {'action': 'add_committee_member', 'member_id': self.voter.user_id})
        self.assertEqual((r.status_code, r.json()['error']), (409, 'applied'))


# ── 13. Officer transition ──────────────────────────────────────────────────

class TransitionTests(_Fix):

    def setUp(self):
        super().setUp()
        self.role = Role.objects.create(name='Sim Treasurer', code='SIMT')
        self.pres.role = self.role
        self.pres.save()
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        self.newbie = member('P-G48HFG')   # a text user id
        self.client.force_login(self.admin)
        self.url = reverse('slating_transition', args=[self.period.id])

    def post(self, **kw):
        return self.client.post(self.url, {'action': 'confirm', f'enable_{self.pres.id}': 'on',
                                           f'member_{self.pres.id}': self.newbie.user_id, 'reviewed': '1', **kw})

    def test_confirmation_lists_the_role_changes_first(self):
        holder = member('sf-holder')
        holder.roles.add(self.role)
        r = self.post(reviewed='')
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'{self.newbie.name} gets the role; removed from {holder.name}.')
        self.assertContains(r, 'name="reviewed" value="1"')
        self.assertFalse(self.fresh().officer_transition_completed)

    def test_pickers_offer_initiated_members_and_the_winners(self):
        from src.view.slating.transition import transition_members
        pledge = member('sf-tp', member_type='Pledge')
        alum = member('sf-ta')
        ParliamentUser.objects.filter(pk=alum.pk).update(member_status='Alumni')
        ids = {m.pk for m in transition_members({self.pres.id: pledge})}
        self.assertIn(pledge.pk, ids)                           # elected, so offered
        self.assertNotIn(alum.pk, ids)
        self.assertNotIn(member('sf-tp2', member_type='Pledge').pk, {m.pk for m in transition_members()})
        r = self.client.post(self.url, {'action': 'confirm', f'enable_{self.pres.id}': 'on',
                                        f'member_{self.pres.id}': alum.user_id, 'reviewed': '1'})
        self.assertFalse(self.fresh().officer_transition_completed)

    def test_results_page_links_the_transition_for_a_site_admin(self):
        self.assertContains(self.client.get(reverse('slating_results', args=[self.period.id])), self.url)

    def test_scheduled_for_next_week_waits(self):
        nxt = timezone.localtime() + timedelta(days=7)
        r = self.post(effective_date=nxt.strftime('%Y-%m-%d'), effective_time='18:00')
        self.assertEqual(r.status_code, 302)
        p = self.fresh()
        self.assertFalse(p.officer_transition_completed)
        self.assertEqual(timezone.localtime(p.officer_transition_at).strftime('%Y-%m-%d %H:%M'),
                         f'{nxt:%Y-%m-%d} 18:00')
        self.assertEqual(p.officer_transition_data, {str(self.pres.id): self.newbie.user_id})
        self.assertFalse(self.newbie.roles.filter(pk=self.role.pk).exists())

    def test_text_user_ids_and_bad_dates(self):
        r = self.post(effective_date='2027-02-30', effective_time='18:00')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.fresh().officer_transition_completed)   # an unreadable date isn't "now"
        r = self.post()
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.fresh().officer_transition_completed)
        self.assertTrue(self.newbie.roles.filter(pk=self.role.pk).exists())


# ── 14. Paused vs closed ────────────────────────────────────────────────────

class VoidTests(_Fix):

    def test_a_closed_round_isnt_paused_or_voidable(self):
        self.vote(self.voter, 'reject')
        close_round(self.fresh(), self.manager)
        self.as_manager()
        r = self.client.get(reverse('slating_vote', args=[self.period.id]))
        self.assertEqual(r.status_code, 302)                       # no "Voting paused / Void this round"
        self.client.post(reverse('slating_reset_votes', args=[self.period.id]))
        self.assertEqual(SlatingRound.objects.get(period=self.period, attempt=1).status, 'failed')

    def test_a_paused_round_can_still_be_voided(self):
        self.status('deliberation')
        r = self.client.get(reverse('slating_vote', args=[self.period.id]))
        self.assertTrue(r.context['voting_paused'])
        self.client.post(reverse('slating_reset_votes', args=[self.period.id]))
        self.assertEqual(SlatingRound.objects.get(period=self.period, attempt=1).status, 'voided')


# ── 16. Status at cast time ─────────────────────────────────────────────────

class StatusAtCastTests(_Fix):

    def test_an_alum_marked_present_cant_vote(self):
        ParliamentUser.objects.filter(pk=self.voter.pk).update(member_status='Alumni')
        with self.assertRaises(SlatingBallotError) as e:
            cast_slate(self.voter, self.period, self.slate, self.cands(), 'approve', [])
        self.assertEqual(e.exception.code, 'not_eligible')
        self.assertFalse(SlatingBallot.objects.exists())


# ── Small ones ───────────────────────────────────────────────────────────────

class SmallTests(_Fix):

    def test_junk_threshold_is_a_validation_error(self):
        self.vote(self.voter, 'reject')   # failed: every position still undecided
        close_round(self.fresh(), self.manager)
        for bad in ('abc', '60.5', '50', '101'):
            with self.assertRaises(RoundError, msg=bad) as e:
                validate_round(self.fresh(), 'individual', [self.vp.id], threshold=bad)
            self.assertEqual(e.exception.code, 'invalid_threshold')

    def test_junk_gpa_is_a_validation_error(self):
        from django.http import QueryDict
        from src.view.slating.apply import save_application
        for i, bad in enumerate(('nan', 'inf', '1e9', '-1')):
            post = QueryDict(mutable=True)
            post.update({'reported_gpa': bad, 'positions_first_choice': str(self.pres.id)})
            app, errors = save_application(self.period, member(f'sf-gpa-{i}'), None, [], [self.pres], post, {}, True)
            self.assertTrue(any('GPA' in e for e in errors), bad)

    def test_ballot_count_is_the_committees(self):
        url = reverse('slating_api_period_status', args=[self.period.id])
        self.client.force_login(member('sf-plain'))
        self.assertNotIn('total_ballots', self.client.get(url).json()['voting'])
        self.client.force_login(self.committee_member)
        self.assertIn('total_ballots', self.client.get(url).json()['voting'])


# ── 18 / 19. Results pages ──────────────────────────────────────────────────

class ResultsPageTests(_Fix):

    def setUp(self):
        super().setUp()
        self.vp2 = member('sf-rvp2')
        self.runoff = SlateCandidate.objects.create(slate=self.slate, position=self.vp, is_runoff=True,
                                                    write_in_member=self.vp2)

    def overview(self):
        from src.view.slating.results import results_overview
        return results_overview(self.fresh(), self.slate)

    def test_slate_elected_positions_have_no_counts_and_runoff_shows_one_winner(self):
        from src.slating_ballots import cast_individual, individual_ballot
        self.vote(self.voter)
        close_round(self.fresh(), self.manager)                     # slate passes; VP run-off still open
        self.status('voting_open', vote_type='individual', ballot_positions=[self.vp.id], abstain_set='1')
        p = self.fresh()
        _, ballot, voted, config = individual_ballot(p, self.slate, self.voter)
        cast_individual(self.voter, p, ballot, voted, config, lambda s: f'c{self.runoff.id}')
        close_round(self.fresh(), self.manager)
        ov = self.overview()
        pres = next(x for x in ov['positions'] if x['title'] == 'President')
        vp = next(x for x in ov['positions'] if x['title'] == 'VP')
        self.assertEqual((pres['how'], pres['tallies']), ('slate', []))    # no "0 approve 0 reject"
        self.assertEqual((vp['how'], vp['winner']), ('round', self.vp2.name))
        self.assertEqual([t['passed'] for t in vp['tallies']], [False, True])
        self.assertEqual(ov['headline'], '2 elected')
        # The app's results: "passed" only for who holds the position.
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='results_published')
        r = self.api(self.voter, 'get', f'slating/periods/{self.period.id}/results/')
        rows = {(c['position'], c['name']): c['passed'] for c in r.json()['candidates']}
        self.assertEqual(rows[('VP', self.manager.name)], False)
        self.assertEqual(rows[('VP', self.vp2.name)], True)
        self.assertEqual(r.json()['headline'], '2 elected')
        page = self.client.get(reverse('slating_results', args=[self.period.id]))
        self.assertContains(page, '2 elected')

    def test_no_verdict_while_voting_is_open_and_counts_any_round(self):
        self.vote(self.voter, 'reject')
        self.as_manager()
        r = self.client.get(reverse('slating_results', args=[self.period.id]))
        self.assertNotContains(r, 'Slate Did Not Pass')
        self.assertNotContains(r, 'Slate Approved')
        self.assertContains(r, 'not final until you close voting')
        self.assertContains(r, '1 of 1 present have voted')
        r = self.client.get(reverse('slating_period_setup', args=[self.period.id]))
        self.assertContains(r, '1 of 1 present have voted')

    def test_participation_is_of_those_present(self):
        from src.view.slating.results import results_summary_data
        for i in range(3):
            SlatingAttendance.objects.create(period=self.period, member=member(f'sf-pp{i}'))
        self.vote(self.voter)
        self.assertEqual(results_summary_data(self.fresh())['participation_rate'], 25.0)   # 1 of 4 present

    def test_in_person_counts_keep_abstains(self):
        from src.view.slating.slate_builder import record_manual_results
        period = SlatingPeriod.objects.create(name='Paper', status='deliberation')
        pos = SlatingPosition.objects.create(period=period, title='Sec', code='S')
        winner = member('sf-ip')
        record_manual_results(period, self.manager, {'vote_mode': 'per_position', f'winner_{pos.id}': winner.user_id,
                                                     f'approve_{pos.id}': '7', f'reject_{pos.id}': '2',
                                                     f'abstain_{pos.id}': '3'})
        from src.view.slating.results import results_overview
        ov = results_overview(period, Slate.objects.get(period=period))
        t = ov['positions'][0]['tallies'][0]
        self.assertEqual((t['approve'], t['reject'], t['abstain'], ov['positions'][0]['how']), (7, 2, 3, 'in_person'))


# ── 20. Approving the slate ─────────────────────────────────────────────────

class ApproveSlateTests(_Fix):

    def setUp(self):
        super().setUp()
        self.build = SlatingPeriod.objects.create(name='Build', status='deliberation', slating_manager=self.manager)
        self.b_pres = SlatingPosition.objects.create(period=self.build, title='President', code='PRES')
        self.b_vpp = SlatingPosition.objects.create(period=self.build, title='VP Programming', code='VPP', display_order=2)
        self.b_app = SlatingApplication.objects.create(period=self.build, applicant=member('sf-b1'), status='interviewed')
        self.as_manager()
        self.url = reverse('slating_build_slate', args=[self.build.id])

    def test_empty_slate_is_refused_and_the_confirmation_says_what_is_filled(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['approve_confirm'], 'Approve this slate (0 of 2 filled; PRES, VPP unfilled)? It goes on the ballot.')
        self.client.post(reverse('slating_approve_slate', args=[self.build.id]), {'slate_type': 'primary'})
        self.assertFalse(Slate.objects.filter(period=self.build, is_approved=True).exists())
        self.client.post(self.url, {'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.b_app.id})
        self.assertContains(self.client.get(self.url), 'Approve this slate (1 of 2 filled; VPP unfilled)? It goes on the ballot.')

    def test_after_approval_the_builder_shows_the_approved_slate(self):
        self.client.post(self.url, {'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.b_app.id})
        self.client.post(reverse('slating_approve_slate', args=[self.build.id]), {'slate_type': 'primary'})
        r = self.client.get(self.url)
        self.assertTrue(r.context['read_only'])
        self.assertEqual(r.context['slate'].slate_type, 'primary')
        self.assertContains(r, 'Edit approved slate')
        self.assertNotContains(r, reverse('slating_approve_slate', args=[self.build.id]))
        self.assertIn('1 candidate · approved', r.context['slate'].label)
        self.assertFalse(Slate.objects.filter(period=self.build, slate_type='draft').exists())
        self.client.post(self.url, {'action': 'unassign', 'assignment_id': r.context['slate'].candidates.get().id})
        self.assertEqual(r.context['slate'].candidates.count(), 1)   # read-only


# ── 21. Opening voting ──────────────────────────────────────────────────────

class OpenVotingTests(_Fix):

    def test_refused_with_no_one_present_and_the_form_says_who_is(self):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='deliberation', current_voting_attempt=0)
        SlatingAttendance.objects.all().delete()
        self.as_manager()
        r = self.client.get(reverse('slating_period_setup', args=[self.period.id]))
        self.assertContains(r, 'Attendance: 0 present')
        self.assertContains(r, reverse('slating_attendance', args=[self.period.id]))
        self.status('voting_open', vote_type='slate')
        self.assertEqual(self.fresh().status, 'deliberation')
        SlatingAttendance.objects.create(period=self.period, member=self.voter)
        r = self.client.get(reverse('slating_period_setup', args=[self.period.id]))
        self.assertContains(r, 'Open voting — round 1, full slate, 1 member present?')
        self.status('voting_open', vote_type='slate')
        self.assertEqual(self.fresh().status, 'voting_open')

    def test_after_a_failed_slate_the_next_round_defaults_to_the_slate(self):
        from src.slating_rounds import election_context
        self.vote(self.voter, 'reject')
        close_round(self.fresh(), self.manager)
        self.assertEqual(election_context(self.fresh())['default_vote_type'], 'slate')


# ── 22. Creating a period ───────────────────────────────────────────────────

class CreatePeriodTests(_Fix):

    def test_manager_and_dates_are_saved_and_the_admin_is_warned(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('slating_create_period'))
        self.assertNotContains(r, 'var _writeInData = ;')
        r = self.client.post(reverse('slating_create_period'), {
            'name': 'Spring', 'academic_term': 'Spring 2027', 'required_approval_percentage': '60',
            'slating_manager': self.manager.user_id, 'nominations_open_at': '2027-01-10T19:00'}, follow=True)
        p = SlatingPeriod.objects.get(name='Spring')
        self.assertEqual(p.slating_manager, self.manager)
        self.assertEqual(timezone.localtime(p.nominations_open_at).strftime('%Y-%m-%d %H:%M'), '2027-01-10 19:00')
        self.assertContains(r, 'this period is now confidential')
        self.assertTrue(p.slating_committee.is_ad_hoc)


# ── 23. The chapter's decision ──────────────────────────────────────────────

class DecisionUiTests(_Fix):

    def test_a_vacant_position_can_be_left_unfilled_and_overrides_are_confirmed(self):
        sec = SlatingPosition.objects.create(period=self.period, title='Secretary', code='SEC')
        self.vote(self.voter)
        close_round(self.fresh(), self.manager)            # slate passes; Secretary has no nominee
        self.as_manager()
        page = self.client.get(reverse('slating_period_setup', args=[self.period.id]))
        self.assertContains(page, 'Leave it unfilled')
        url = reverse('slating_position_action', args=[self.period.id])
        self.client.post(url, {'action': 'decide', 'position_id': sec.id, 'choice': 'unfilled', 'reason': 'No one ran'})
        self.assertTrue(next(s for s in position_states(self.period) if s.position == sec).unfilled)
        self.assertEqual(self.fresh().status, 'voting_closed')
        # Replacing an elected officer needs the confirmation's override.
        with self.assertRaises(RoundError) as e:
            decide_manually(self.fresh(), self.manager, self.pres.id, 'unfilled', 'changed our minds')
        self.assertEqual(e.exception.code, 'confirm_override')
        decide_manually(self.fresh(), self.manager, self.pres.id, 'unfilled', 'changed our minds', override=True)
        self.assertTrue(next(s for s in position_states(self.period) if s.position == self.pres).unfilled)


# ── 24. One Publish ─────────────────────────────────────────────────────────

@mock.patch('src.notification_service.notify_all_active_members')
class PublishPathTests(_Fix):

    def test_setup_page_publish_is_the_results_pages(self, notify):
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_closed')
        self.status('results_published')
        p = self.fresh()
        self.assertEqual(p.status, 'results_published')
        self.assertIsNotNone(p.results_publish_at)
        self.assertEqual(notify.call_args.args[0], 'slating_results')
        self.assertTrue(SlatingActivity.objects.filter(period=p, action='results_published').exists())

    def test_refusal_says_what_is_undecided(self, notify):
        from src.view.slating.results import publish_refusal
        self.vote(self.voter, 'reject')
        close_round(self.fresh(), self.manager)
        msg = publish_refusal(self.fresh())
        self.assertIn('Still undecided: President, VP.', msg)
        SlatingPeriod.objects.filter(pk=self.period.pk).update(status='voting_open')
        self.assertIn('close it first', publish_refusal(self.fresh()))
