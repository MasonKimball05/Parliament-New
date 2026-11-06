"""
The slate builder in the app (11-04-26): building, approving, previewing,
copying and editing the slate, write-ins, manual results and the
``slate_candidates`` API, each through the web view's own helpers
(src/view/slating/slate_builder.py).

These check the web's permissions (chair vs committee vs member, the locked
period), its status gates, its validation and its side effects (application
status, SlatingActivity, round history), and that the web pages still behave.

    python manage.py test src.tests.mobile.test_slating_slate
"""
from django.urls import reverse

from src.models import (
    Slate, SlateCandidate, SlatingActivity, SlatingApplication, SlatingPeriod, SlatingPosition, SlatingRound,
    SlatingVote,
)
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.tests.slating.test_slating_review_fixes import member


class _SlateBase(_AppBase):
    """``self.period`` (from _Base) is voting_open with an approved slate; ``self.build`` is in deliberation."""

    def setUp(self):
        super().setUp()
        self.build = SlatingPeriod.objects.create(name='Spring', status='deliberation', slating_manager=self.manager,
                                                  required_approval_percentage=66)
        self.b_pres = SlatingPosition.objects.create(period=self.build, title='President', code='P', display_order=1)
        self.b_sec = SlatingPosition.objects.create(period=self.build, title='Secretary', code='S', display_order=2)
        SlatingPosition.objects.create(period=self.build, title='Hidden', code='H', is_active=False)
        self.alice = member('ss-alice')
        self.bob = member('ss-bob')
        self.a_app = SlatingApplication.objects.create(
            period=self.build, applicant=self.alice, status='interviewed',
            position_preferences={'first_choice': [self.b_sec.id], 'second_choice': [self.b_pres.id]})
        self.b_app = SlatingApplication.objects.create(period=self.build, applicant=self.bob, status='submitted')
        SlatingApplication.objects.create(period=self.build, applicant=member('ss-draft'), status='draft')
        SlatingApplication.objects.create(period=self.build, applicant=member('ss-gone'), status='withdrawn')

    def p(self, period, rest):
        return f'slating/periods/{period.id}/{rest}'

    def err(self, r):
        return r.status_code, r.json().get('error')


class PermissionTests(_SlateBase):

    def test_only_the_periods_managers(self):
        # 11-06-26: the committee builds the slate (build, write-ins, preview,
        # the candidates); editing the approved slate, in-person results and
        # approving stay with the managers.
        committee_paths = ['slate/build/', 'slate/write-ins/',
                           f'slates/{self.slate.id}/', f'slates/{self.slate.id}/candidates/']
        manager_paths = ['slate/edit/', 'manual-results/']
        for rest in committee_paths + manager_paths:
            path = self.p(self.period, rest)
            self.assertEqual(self.call('get', path).status_code, 403, rest)                       # a member
            self.assertEqual(self.call('get', path, user=self.committee_member).status_code,
                             200 if rest in committee_paths else 403, rest)
            self.assertEqual(self.call('get', path, user=self.admin).status_code, 403, rest)      # locked
            # 11-05-26: voting has started online, so manual results are refused (409), not forbidden.
            self.assertEqual(self.call('get', path, user=self.manager).status_code,
                             409 if rest == 'manual-results/' else 200, rest)
        r = self.call('post', self.p(self.period, 'slate/approve/'), {}, user=self.committee_member)
        self.assertEqual(r.status_code, 403)
        build = self.call('get', self.p(self.period, 'slate/build/'), user=self.committee_member).json()
        self.assertFalse(build['can_approve'])
        self.assertTrue(self.call('get', self.p(self.period, 'slate/build/'), user=self.manager).json()['can_approve'])

    def test_site_admin_in_an_unlocked_period(self):
        period = SlatingPeriod.objects.create(name='Open', status='deliberation')
        self.assertEqual(self.call('get', self.p(period, 'slate/build/'), user=self.admin).status_code, 200)
        self.assertEqual(self.call('get', self.p(period, 'slate/build/')).status_code, 403)

    def test_a_pledge_manager_gets_nothing(self):
        pledge = member('ss-pledge', member_type='Pledge')
        self.build.slating_manager = pledge
        self.build.save()
        if not pledge.is_pledge:
            self.skipTest('member_type Pledge is not how pledges are marked here')
        self.assertEqual(self.call('get', self.p(self.build, 'slate/build/'), user=pledge).status_code, 403)

    def test_approved_slate_is_for_voting_members_once_voting_opens(self):
        r = self.call('get', self.p(self.period, 'slate/'))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body['public'])
        self.assertNotIn('can_approve', body)
        self.assertEqual({c['name'] for c in body['candidates']}, {self.voter.name, self.manager.name})
        advisor = member('ss-advisor', member_type='Advisor')
        self.assertEqual(self.err(self.call('get', self.p(self.period, 'slate/'), user=advisor)),
                         (403, 'not_eligible'))
        self.period.status = 'deliberation'
        self.period.save()
        self.assertEqual(self.err(self.call('get', self.p(self.period, 'slate/'))), (409, 'not_public'))
        self.period.status = 'results_published'
        self.period.save()
        self.slate.is_approved = False
        self.slate.save()
        self.assertEqual(self.err(self.call('get', self.p(self.period, 'slate/'))), (404, 'no_slate'))

    def test_hub_and_chair_say_what_is_open(self):
        hub = {p['id']: p for p in self.call('get', 'slating/').json()['periods']}
        self.assertTrue(hub[self.period.id]['can_view_slate'])
        self.assertFalse(hub[self.build.id]['can_view_slate'])
        tools = self.call('get', self.p(self.period, 'chair/'), user=self.manager).json()['slate_tools']
        self.assertEqual(tools, {'can_build': True, 'can_edit': True, 'can_write_ins': True,
                                 'can_manual_results': True})
        tools = self.call('get', self.p(self.build, 'chair/'), user=self.manager).json()['slate_tools']
        self.assertEqual((tools['can_edit'], tools['can_write_ins']), (False, False))


class BuildTests(_SlateBase):

    def post(self, body, rest='slate/build/'):
        return self.call('post', self.p(self.build, rest), body, user=self.manager)

    def test_get_makes_the_draft_and_lists_available_applications(self):
        body = self.call('get', self.p(self.build, 'slate/build/'), user=self.manager).json()
        draft = Slate.objects.get(period=self.build, slate_type='draft')
        self.assertEqual((body['slate']['id'], body['slate']['name']), (draft.id, 'Draft Slate'))
        self.assertEqual([p['title'] for p in body['positions']], ['President', 'Secretary'])   # active only
        self.assertEqual([a['applicant'] for a in body['available']], [self.alice.name, self.bob.name])
        alice = body['available'][0]
        self.assertEqual(alice['first_choice_position'], self.b_sec.id)
        self.assertEqual(alice['tiers'], {str(self.b_sec.id): 'first_choice', str(self.b_pres.id): 'second_choice'})
        self.assertEqual((body['primary_filled_count'], body['all_primary_filled']), (0, False))
        self.call('get', self.p(self.build, 'slate/build/'), user=self.manager)
        self.assertEqual(Slate.objects.filter(period=self.build, slate_type='draft').count(), 1)

    def test_assign_runoff_and_unassign(self):
        r = self.post({'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.a_app.id})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['message'], f'{self.alice.name} assigned as primary candidate for President.')
        self.assertEqual(body['positions'][0]['primary']['name'], self.alice.name)
        self.assertEqual([a['id'] for a in body['available']], [self.b_app.id])
        self.a_app.refresh_from_db()
        self.assertEqual(self.a_app.status, 'slated')

        body = self.post({'action': 'assign_runoff', 'position_id': self.b_pres.id,
                          'application_id': self.b_app.id}).json()
        self.assertIn('runoff candidate', body['message'])
        runoff = body['positions'][0]['runoff']
        self.assertTrue(runoff['is_runoff'])
        self.assertEqual(body['primary_filled_count'], 1)

        body = self.post({'action': 'save_notes', 'assignment_id': runoff['id'], 'notes': '  strong  '}).json()
        self.assertEqual(body['positions'][0]['runoff']['notes'], 'strong')

        body = self.post({'action': 'unassign', 'assignment_id': runoff['id']}).json()
        self.assertEqual(body['message'], f'{self.bob.name} removed from President.')
        self.assertIsNone(body['positions'][0]['runoff'])
        self.b_app.refresh_from_db()
        self.assertEqual(self.b_app.status, 'interviewed')

    def test_reassigning_a_slot_replaces_it(self):
        self.post({'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.a_app.id})
        self.post({'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.b_app.id})
        draft = Slate.objects.get(period=self.build, slate_type='draft')
        self.assertEqual(list(draft.candidates.values_list('application_id', flat=True)), [self.b_app.id])

    def test_validation(self):
        self.assertEqual(self.err(self.post({'action': 'nope'})), (400, 'invalid_input'))
        self.assertEqual(self.err(self.post({'action': 'assign', 'position_id': 'x', 'application_id': 1})),
                         (400, 'invalid_input'))
        self.assertEqual(self.err(self.post({'action': 'assign', 'position_id': self.b_pres.id})),
                         (400, 'invalid_input'))
        # Another period's application or position: not found.
        r = self.post({'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.app.id})
        self.assertEqual(r.status_code, 404)
        r = self.post({'action': 'assign', 'position_id': self.pres.id, 'application_id': self.a_app.id})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(SlateCandidate.objects.filter(slate__period=self.build).exists())

    def test_approve_the_draft(self):
        old = Slate.objects.create(period=self.build, slate_type='primary', is_approved=True, name='Old')
        # 11-05-26: with an approved slate and no draft the builder is read-only; a new draft is started (Copy).
        Slate.objects.create(period=self.build, slate_type='draft', name='Draft Slate')
        self.post({'action': 'assign', 'position_id': self.b_pres.id, 'application_id': self.a_app.id})
        r = self.post({'slate_type': 'primary'}, 'slate/approve/')
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['message'], 'Slate "Primary Slate" approved.')
        self.assertEqual(body['warning'], 'Slate approved with 1 unfilled position(s): Secretary. '
                                          'These will be decided in person.')
        slate = Slate.objects.get(id=body['slate']['id'])
        self.assertEqual((slate.slate_type, slate.is_approved, slate.approved_by), ('primary', True, self.manager))
        old.refresh_from_db()
        self.assertEqual(old.slate_type, 'alternative')
        log = SlatingActivity.objects.get(period=self.build, action='slate_created')
        self.assertEqual(log.metadata, {'slate_id': slate.id, 'slate_type': 'primary'})

    def test_approve_by_id_with_no_warning(self):
        slate = Slate.objects.create(period=self.build, slate_type='alternative', name='Alt')
        for pos, app in ((self.b_pres, self.a_app), (self.b_sec, self.b_app)):
            SlateCandidate.objects.create(slate=slate, position=pos, application=app)
        body = self.post({'slate_id': slate.id, 'slate_type': 'alternative', 'name': 'Plan B'},
                         'slate/approve/').json()
        self.assertIsNone(body['warning'])
        slate.refresh_from_db()
        self.assertEqual((slate.name, slate.slate_type, slate.is_approved), ('Plan B', 'alternative', True))

    def test_approve_without_a_draft_is_not_found(self):
        self.assertEqual(self.post({}, 'slate/approve/').status_code, 404)

    def test_preview_copy_and_the_candidates_api(self):
        SlateCandidate.objects.create(slate=self.slate, position=self.vp, write_in_member=self.manager,
                                      is_runoff=True)
        body = self.call('get', self.p(self.period, f'slates/{self.slate.id}/'), user=self.manager).json()
        self.assertFalse(body['public'])
        self.assertFalse(body['can_approve'])
        self.assertEqual(len(body['candidates']), 3)

        r = self.call('get', self.p(self.period, f'slates/{self.slate.id}/candidates/'), user=self.manager)
        names = {c['applicant']['name'] for c in r.json()['candidates']}
        self.assertEqual(names, {self.voter.name, self.manager.name})

        # The web view the app's copy matches
        self.client.force_login(self.manager)
        web = self.client.get(reverse('slating_api_slate_candidates', args=[self.period.id, self.slate.id])).json()
        self.assertEqual(web, r.json())

        self.call('get', self.p(self.period, 'slate/build/'), user=self.manager)   # the draft exists
        r = self.call('post', self.p(self.period, f'slates/{self.slate.id}/copy/'), {}, user=self.manager)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['message'], f'Created new draft from "{self.slate.name}".')
        copy = Slate.objects.get(id=r.json()['slate']['id'])
        self.assertEqual((copy.slate_type, copy.name), ('draft', f'Copy of {self.slate.name}'))
        self.assertEqual(copy.candidates.count(), 3)
        self.assertEqual(copy.candidates.filter(is_runoff=True).count(), 1)
        self.assertEqual(copy.candidates.filter(write_in_member=self.manager).count(), 2)
        # The builder opens on the copy (a second draft used to break it).
        body = self.call('get', self.p(self.period, 'slate/build/'), user=self.manager).json()
        self.assertEqual(body['slate']['id'], copy.id)
        r = self.client.get(reverse('slating_build_slate', args=[self.period.id]))
        self.assertEqual(r.status_code, 200)

    def test_preview_of_another_periods_slate_is_not_found(self):
        r = self.call('get', self.p(self.build, f'slates/{self.slate.id}/'), user=self.manager)
        self.assertEqual(r.status_code, 404)


class EditTests(_SlateBase):

    def setUp(self):
        super().setUp()
        self.voter2 = member('ss-voter2')
        self.path = self.p(self.period, 'slate/edit/')

    def pause(self):
        self.period.status = 'deliberation'
        self.period.save()

    def test_get(self):
        body = self.call('get', self.path, user=self.manager).json()
        self.assertFalse(body['voting_paused'])
        self.assertEqual([r['position']['title'] for r in body['rows']], ['President', 'VP'])
        self.assertEqual(body['rows'][1]['current']['write_in'], True)
        self.assertEqual([a['id'] for a in body['applications']], [self.app.id])
        self.assertIn(self.voter2.user_id, [m['user_id'] for m in body['members']])

    def test_replace_with_a_write_in_keeps_the_round_history(self):
        SlatingVote.objects.create(period=self.period, slate_candidate=self.c_pres, voting_attempt=1,
                                   vote_choice='reject', vote_hash='h1')
        self.pause()   # 11-05-26: not while the round is open
        r = self.call('post', self.path, {'position_id': self.pres.id, 'candidate_type': 'write_in',
                                          'member_id': self.voter2.user_id}, user=self.manager)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['message'], f'President updated: {self.voter.name} → {self.voter2.name}.')
        self.assertEqual(r.json()['rows'][0]['current']['name'], self.voter2.name)
        self.assertFalse(SlateCandidate.objects.filter(id=self.c_pres.id).exists())
        self.assertTrue(SlatingActivity.objects.filter(period=self.period, action='slate_edited').exists())
        self.assertEqual(SlatingRound.objects.get(period=self.period, attempt=1).events[0]['reject'], 1)

    def test_replace_with_an_applicant(self):
        other = SlatingApplication.objects.create(period=self.period, applicant=self.voter2, status='interviewed')
        self.pause()   # 11-05-26: not while the round is open
        r = self.call('post', self.path, {'position_id': self.vp.id, 'candidate_type': 'applicant',
                                          'application_id': other.id}, user=self.manager)
        self.assertEqual(r.json()['rows'][1]['current']['application_id'], other.id)

    def test_an_incomplete_choice_changes_nothing(self):
        for body in ({'position_id': self.pres.id, 'candidate_type': 'write_in'},
                     {'position_id': self.pres.id, 'candidate_type': 'applicant', 'member_id': 'x'},
                     {'candidate_type': 'write_in', 'member_id': self.voter2.user_id}):
            self.assertEqual(self.err(self.call('post', self.path, body, user=self.manager)), (400, 'invalid_input'))
        self.assertTrue(SlateCandidate.objects.filter(id=self.c_pres.id).exists())

    def test_only_while_voting_is_open_or_paused(self):
        self.period.status = 'deliberation'
        self.period.save()
        self.assertEqual(self.call('get', self.path, user=self.manager).json()['voting_paused'], True)
        self.period.current_voting_attempt = 0
        self.period.save()
        self.assertEqual(self.err(self.call('get', self.path, user=self.manager)), (409, 'not_voting'))
        self.period.status = 'voting_open'
        self.period.save()
        self.slate.is_approved = False
        self.slate.save()
        self.assertEqual(self.err(self.call('get', self.path, user=self.manager)), (409, 'no_slate'))


class WriteInTests(_SlateBase):

    def setUp(self):
        super().setUp()
        self.path = self.p(self.period, 'slate/write-ins/')
        self.voter2 = member('ss-voter2')

    def test_get(self):
        body = self.call('get', self.path, user=self.manager).json()
        self.assertTrue(body['available'])
        self.assertEqual([w['name'] for w in body['write_ins']], [self.manager.name])
        self.assertEqual(body['blank_positions'], [{'id': self.vp.id, 'title': 'VP', 'has_write_in': True}])
        self.assertIn(self.voter.user_id, body['slated_ids'])
        self.assertIn(self.voter2.user_id, [m['user_id'] for m in body['members']])

    def test_assign_and_remove(self):
        r = self.call('post', self.path, {'position_id': self.vp.id, 'member_id': self.voter2.user_id},
                      user=self.manager)
        self.assertEqual(self.err(r), (409, 'position_filled'))
        r = self.call('post', self.path + 'remove/', {'candidate_id': self.c_vp.id}, user=self.manager)
        self.assertEqual(r.json()['message'], f'{self.manager.name} removed from VP.')
        self.assertFalse(SlateCandidate.objects.filter(id=self.c_vp.id).exists())
        r = self.call('post', self.path, {'position_id': self.vp.id, 'member_id': self.voter2.user_id},
                      user=self.manager)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['message'], f'{self.voter2.name} added as write-in candidate for VP. '
                                              'They will be voted on individually.')
        self.assertEqual([w['user_id'] for w in r.json()['write_ins']], [self.voter2.user_id])
        log = SlatingActivity.objects.get(period=self.period, action='position_added')
        self.assertEqual(log.details, f'Write-in: {self.voter2.name} assigned to VP')

    def test_an_applicant_cant_be_removed_as_a_write_in(self):
        r = self.call('post', self.path + 'remove/', {'candidate_id': self.c_pres.id}, user=self.manager)
        self.assertEqual(r.status_code, 404)

    def test_only_in_deliberation_or_voting(self):
        self.period.status = 'voting_closed'
        self.period.save()
        body = self.call('get', self.path, user=self.manager).json()
        self.assertFalse(body['available'])
        self.assertEqual(body['blank_positions'], [])
        r = self.call('post', self.path, {'position_id': self.vp.id, 'member_id': self.voter2.user_id},
                      user=self.manager)
        self.assertEqual(self.err(r), (409, 'not_allowed'))
        self.period.status = 'deliberation'
        self.period.save()
        self.slate.is_approved = False
        self.slate.save()
        r = self.call('post', self.path, {'position_id': self.vp.id, 'member_id': self.voter2.user_id},
                      user=self.manager)
        self.assertEqual(self.err(r), (409, 'no_slate'))


class ManualResultsTests(_SlateBase):

    def setUp(self):
        super().setUp()
        self.path = self.p(self.build, 'manual-results/')

    def post(self, body):
        return self.call('post', self.path, body, user=self.manager)

    def test_get(self):
        body = self.call('get', self.path, user=self.manager).json()
        self.assertEqual([p['title'] for p in body['positions']], ['President', 'Secretary'])
        self.assertIn(self.alice.user_id, [m['user_id'] for m in body['members']])
        self.assertEqual(body['period']['required_percentage'], 66)

    def test_every_position_needs_a_winner(self):
        r = self.post({'vote_mode': 'none', f'winner_{self.b_pres.id}': self.alice.user_id,
                       f'winner_{self.b_sec.id}': 'nobody'})
        self.assertEqual(self.err(r), (400, 'invalid_input'))
        self.assertEqual(r.json()['detail'], 'Invalid member for Secretary.')
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, 'deliberation')
        self.assertFalse(SlateCandidate.objects.filter(slate__period=self.build).exists())

    def test_no_vote_records(self):
        r = self.post({'vote_mode': 'none', f'winner_{self.b_pres.id}': self.alice.user_id,
                       f'winner_{self.b_sec.id}': self.bob.user_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['message'], 'Results recorded. You can now publish them.')
        self.build.refresh_from_db()
        self.assertEqual(self.build.status, 'voting_closed')
        slate = Slate.objects.get(period=self.build, slate_type='primary')
        self.assertTrue(slate.is_approved)
        self.assertEqual(set(slate.candidates.values_list('individual_passed', flat=True)), {True})
        log = SlatingActivity.objects.get(period=self.build, action='voting_closed')
        self.assertEqual(log.metadata, {'manual': True, 'vote_mode': 'none'})
        # Then it's closed
        self.assertEqual(self.err(self.call('get', self.path, user=self.manager)), (409, 'already_recorded'))

    def test_per_position(self):
        self.post({'vote_mode': 'per_position',
                   f'winner_{self.b_pres.id}': self.alice.user_id, f'approve_{self.b_pres.id}': 7,
                   f'reject_{self.b_pres.id}': 3,
                   f'winner_{self.b_sec.id}': self.bob.user_id, f'approve_{self.b_sec.id}': 6,
                   f'reject_{self.b_sec.id}': 4})
        self.build.refresh_from_db()
        self.assertEqual(self.build.vote_type, 'individual')
        got = {c.position_id: (c.individual_passed, c.individual_votes_for)
               for c in SlateCandidate.objects.filter(slate__period=self.build)}
        self.assertEqual(got, {self.b_pres.id: (True, 7), self.b_sec.id: (False, 6)})

    def test_overall(self):
        self.post({'vote_mode': 'overall', 'overall_approve': 8, 'overall_reject': 2, 'overall_abstain': 1,
                   f'winner_{self.b_pres.id}': self.alice.user_id, f'winner_{self.b_sec.id}': self.bob.user_id})
        slate = Slate.objects.get(period=self.build, slate_type='primary')
        self.assertEqual((slate.total_votes, slate.approval_votes, slate.passed), (11, 8, True))


class WebPagesTests(_SlateBase):
    """The web pages still do what they did, now through the shared helpers."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)

    def test_builder_assign_and_approve(self):
        url = reverse('slating_build_slate', args=[self.build.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'action': 'assign', 'position_id': self.b_pres.id,
                                   'application_id': self.a_app.id}, follow=True)
        self.assertContains(r, 'assigned as primary candidate for President')
        r = self.client.post(reverse('slating_approve_slate', args=[self.build.id]), {'slate_type': 'primary'})
        self.assertRedirects(r, reverse('slating_period_setup', args=[self.build.id]), fetch_redirect_response=False)
        self.assertTrue(Slate.objects.filter(period=self.build, slate_type='primary', is_approved=True).exists())

    def test_write_in_refusal_and_manual_results_errors(self):
        url = reverse('slating_assign_write_in', args=[self.build.id])
        r = self.client.post(url, {'position_id': self.b_pres.id, 'member_id': self.bob.user_id})
        self.assertRedirects(r, reverse('slating_period_setup', args=[self.build.id]), fetch_redirect_response=False)
        self.assertFalse(SlateCandidate.objects.filter(slate__period=self.build).exists())
        url = reverse('slating_manual_results', args=[self.build.id])
        r = self.client.post(url, {'vote_mode': 'none'})
        self.assertRedirects(r, url, fetch_redirect_response=False)
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertEqual(msgs, ['No approved primary slate found.',   # the write-in, not yet shown
                                'Select a winner for President.', 'Select a winner for Secretary.'])
        # The refused form left no empty approved slate behind.
        self.assertFalse(Slate.objects.filter(period=self.build, slate_type='primary', is_approved=True).exists())

    def test_edit_page_invalid_replacement_message(self):
        self.period.status = 'deliberation'   # 11-05-26: paused (no swaps while the round is open)
        self.period.save()
        url = reverse('slating_edit_approved_slate', args=[self.period.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'position_id': self.vp.id, 'candidate_type': 'applicant'}, follow=True)
        self.assertContains(r, 'Invalid replacement')
        # The refusal leaves the current candidate in place rather than vacating the position.
        self.assertTrue(self.slate.candidates.filter(position=self.vp, is_runoff=False).exists())
