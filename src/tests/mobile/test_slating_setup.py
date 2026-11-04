"""
Election setup in the app (11-04-26): creating and editing a slating period,
its positions, and transferring the committee's admin.

Each endpoint runs the web view's own helpers, so these check the web's
permissions (site admin vs chair, the locked-period rule, the transfer
page's own rule), its validation, its side effects (activity log,
notifications, the ad hoc committee), and that the web views still behave
the same after the move into helpers.

    python manage.py test src.tests.mobile.test_slating_setup
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Committee, Role, Slate, SlateCandidate, SlatingActivity, SlatingPeriod, SlatingPosition,
)
from src.tests.mobile.test_slating_batch5 import _AppBase
from src.tests.slating.test_slating_review_fixes import member


class _SetupBase(_AppBase):

    def setUp(self):
        super().setUp()
        # Unlocked: no slating manager and a committee with no admin, so a site admin may manage it.
        self.open_committee = Committee.objects.create(name='Slating (open)', code='SLOPEN', is_active=True)
        self.unlocked = SlatingPeriod.objects.create(name='Spring', academic_term='Spring 2027', status='setup',
                                                     slating_committee=self.open_committee)

    def setup_path(self, period=None):
        return f'slating/periods/{(period or self.period).id}/setup/'

    def positions_path(self, period=None, rest=''):
        return f'slating/periods/{(period or self.period).id}/positions/{rest}'

    def settings_body(self, **kw):
        body = {
            'action': 'save', 'name': 'Fall elections', 'academic_term': 'Fall 2027', 'description': 'Notes',
            'required_approval_percentage': '70', 'min_gpa_requirement': '2.75', 'gpa_level_2_threshold': '0.25',
            'nominations_open_at': '2027-01-10T19:00:00-06:00', 'nominations_close_at': '2027-01-20T19:00:00-06:00',
            'voting_open_at': '', 'voting_close_at': '',
        }
        body.update(kw)
        return body


# ── Permissions and the locked-period rule ──────────────────────────────────

class SetupPermissionTests(_SetupBase):

    def test_only_the_periods_managers(self):
        self.assertEqual(self.call('get', self.setup_path(), user=self.manager).status_code, 200)
        for user in (self.voter, self.committee_member):
            r = self.call('get', self.setup_path(), user=user)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertEqual(self.call('get', self.positions_path(), user=self.voter).status_code, 403)
        r = self.call('post', f'{self.setup_path()}status/', {'status': 'deliberation'}, user=self.voter)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(SlatingPeriod.objects.get(id=self.period.id).status, 'voting_open')

    def test_locked_period_shuts_out_a_site_admin(self):
        # Locked (a slating manager is set): site admin alone isn't enough, as on the web.
        self.assertEqual(self.call('get', self.setup_path(), user=self.admin).status_code, 403)
        self.assertEqual(self.call('get', self.positions_path(), user=self.admin).status_code, 403)
        web = Client()
        web.force_login(self.admin)
        self.assertEqual(web.get(reverse('slating_period_setup', args=[self.period.id])).status_code, 302)
        # Unlocked: he may.
        self.assertEqual(self.call('get', self.setup_path(self.unlocked), user=self.admin).status_code, 200)
        # Naming a manager locks it, and the admin's access goes with it.
        r = self.call('post', self.setup_path(self.unlocked),
                      self.settings_body(slating_manager=self.manager.user_id), user=self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.call('get', self.setup_path(self.unlocked), user=self.admin).status_code, 403)
        self.assertEqual(self.call('get', self.setup_path(self.unlocked), user=self.manager).status_code, 200)

    def test_committee_chair_manages(self):
        chair = member('ss-chair')
        self.committee.chairs.add(chair)
        self.assertEqual(self.call('get', self.setup_path(), user=chair).status_code, 200)
        pledge = member('ss-pledge', member_type='Pledge')
        self.committee.chairs.add(pledge)
        self.assertEqual(self.call('get', self.setup_path(), user=pledge).status_code, 403)

    def test_create_is_site_admins_only(self):
        body = {'name': 'Winter', 'academic_term': 'Winter 2027', 'description': '',
                'min_gpa_requirement': '2.5', 'gpa_level_2_threshold': '0.2', 'required_approval_percentage': '60'}
        r = self.call('post', 'slating/periods/new/', body, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        r = self.call('post', 'slating/periods/new/', body, user=self.admin)
        self.assertEqual(r.status_code, 201, r.content)
        period = SlatingPeriod.objects.get(id=r.json()['period_id'])
        self.assertEqual((period.name, period.status, period.created_by), ('Winter', 'setup', self.admin))
        self.assertTrue(period.slating_committee.is_ad_hoc)
        self.assertTrue(period.slating_committee.is_slating_committee)
        self.assertTrue(SlatingActivity.objects.filter(period=period, action='period_created').exists())
        self.assertEqual(r.json()['message'], 'Slating period "Winter" created successfully.')

    def test_create_validation(self):
        before = SlatingPeriod.objects.count()
        r = self.call('post', 'slating/periods/new/', {'name': 'X', 'academic_term': ' ',
                                                       'min_gpa_requirement': '2.5', 'gpa_level_2_threshold': '0.2',
                                                       'required_approval_percentage': '160'}, user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('Academic term is required.', r.json()['detail'])
        self.assertIn('Required approval % must be between 1 and 100.', r.json()['detail'])
        self.assertEqual(SlatingPeriod.objects.count(), before)

    def test_hub_says_who_can_create(self):
        self.assertTrue(self.call('get', 'slating/', user=self.admin).json()['can_create_period'])
        self.assertFalse(self.call('get', 'slating/', user=self.manager).json()['can_create_period'])


# ── The setup page ───────────────────────────────────────────────────────────

class SetupPageTests(_SetupBase):

    def test_matches_the_web_page(self):
        admin = member('ss-cadmin')
        self.committee.admin = admin
        self.committee.save()
        body = self.call('get', self.setup_path(), user=self.manager).json()
        web = Client()
        web.force_login(self.manager)
        ctx = web.get(reverse('slating_period_setup', args=[self.period.id])).context
        self.assertEqual([m['user_id'] for m in body['committee']['members']],
                         [m.user_id for m in ctx['committee_members']])
        self.assertTrue(body['committee']['members'][0]['is_admin'])
        self.assertEqual(body['counts']['positions'], ctx['position_count'])
        self.assertEqual(body['counts']['applications'], ctx['application_count'])
        self.assertEqual([m['user_id'] for m in body['eligible_managers']],
                         [m.user_id for m in ctx['eligible_managers']])
        self.assertEqual(body['default_positions'], [t for _, _, t in ctx['default_positions']])
        self.assertEqual(body['period']['manager']['user_id'], self.manager.user_id)
        self.assertEqual(body['next_status']['status'], 'deliberation')   # voting is open: Pause voting
        self.assertFalse(body['can_set_manager'])
        self.assertFalse(body['can_delete'])
        self.assertFalse(body['can_transfer_admin'])   # only the committee's admin or a site admin

    def test_backfills_the_committee(self):
        bare = SlatingPeriod.objects.create(name='Old', academic_term='2020', status='setup')
        body = self.call('get', self.setup_path(bare), user=self.admin).json()
        bare.refresh_from_db()
        self.assertTrue(bare.slating_committee.is_ad_hoc)
        self.assertTrue(body['committee']['exists'])

    def test_date_based_status_change_on_load(self):
        self.unlocked.nominations_open_at = timezone.now() - timedelta(hours=1)
        self.unlocked.save()
        body = self.call('get', self.setup_path(self.unlocked), user=self.admin).json()
        self.assertEqual(body['period']['status'], 'nominations_open')
        self.assertIn('Status automatically updated to "Nominations Open"', body['notice'])

    def test_save_settings(self):
        r = self.call('post', self.setup_path(), self.settings_body(slating_manager=''), user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Period settings saved.')
        p = SlatingPeriod.objects.get(id=self.period.id)
        self.assertEqual((p.name, p.academic_term, p.description), ('Fall elections', 'Fall 2027', 'Notes'))
        self.assertEqual(p.required_approval_percentage, 70)
        self.assertEqual(float(p.min_gpa_requirement), 2.75)
        self.assertEqual(p.nominations_open_at, datetime(2027, 1, 11, 1, 0, tzinfo=dt_timezone.utc))
        self.assertIsNone(p.voting_open_at)
        # Only a site admin changes the manager: his blank was ignored.
        self.assertEqual(p.slating_manager, self.manager)

    def test_save_validation_changes_nothing(self):
        for bad in ({'name': ''}, {'required_approval_percentage': '0'}, {'min_gpa_requirement': '5'},
                    {'gpa_level_2_threshold': 'x'}, {'voting_open_at': 'next tuesday'}):
            r = self.call('post', self.setup_path(), self.settings_body(**bad), user=self.manager)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), bad)
        self.assertEqual(SlatingPeriod.objects.get(id=self.period.id).name, 'Fall')

    def test_committee_members(self):
        newbie = member('ss-new')
        r = self.call('post', self.setup_path(), {'action': 'add_committee_member', 'member_id': newbie.user_id},
                      user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], f'{newbie.name} added to slating committee.')
        self.assertIn(newbie.user_id, [m['user_id'] for m in r.json()['committee']['members']])
        self.assertTrue(self.committee.members.filter(pk=newbie.pk).exists())
        r = self.call('post', self.setup_path(), {'action': 'add_committee_member', 'member_id': 'nobody'},
                      user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (404, 'not_found'))
        r = self.call('post', self.setup_path(), {'action': 'remove_committee_member', 'member_id': newbie.user_id},
                      user=self.manager)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.committee.members.filter(pk=newbie.pk).exists())

    def test_delete_is_site_admins_only(self):
        r = self.call('post', self.setup_path(), {'action': 'delete'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertTrue(SlatingPeriod.objects.filter(id=self.period.id).exists())
        bare = SlatingPeriod.objects.create(name='Gone', academic_term='2020', status='setup')
        self.call('get', self.setup_path(bare), user=self.admin)   # backfills its ad hoc committee
        committee_id = SlatingPeriod.objects.get(id=bare.id).slating_committee_id
        r = self.call('post', self.setup_path(bare), {'action': 'delete'}, user=self.admin)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['deleted'])
        self.assertFalse(SlatingPeriod.objects.filter(id=bare.id).exists())
        self.assertFalse(Committee.objects.filter(id=committee_id).exists())
        # A committee that isn't ad hoc survives.
        r = self.call('post', self.setup_path(self.unlocked), {'action': 'delete'}, user=self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Committee.objects.filter(id=self.open_committee.id).exists())

    def test_bad_action(self):
        r = self.call('post', self.setup_path(), {'action': 'publish'}, user=self.manager)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))


class StatusTests(_SetupBase):

    def status(self, status, period=None, user=None):
        return self.call('post', f'{self.setup_path(period or self.unlocked)}status/', {'status': status},
                         user=user or self.admin)

    def test_nominations_need_a_position_then_announce(self):
        r = self.status('nominations_open')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'no_positions'))
        self.assertEqual(r.json()['detail'], 'Add at least one position before opening nominations.')
        SlatingPosition.objects.create(period=self.unlocked, title='Treasurer', code='T')
        with mock.patch('src.view.slating.period_setup._notify_nominations_open') as notify:
            r = self.status('nominations_open')
        self.assertEqual(r.status_code, 200, r.content)
        notify.assert_called_once()
        self.assertEqual(r.json()['period']['status'], 'nominations_open')
        self.assertEqual(r.json()['next_status']['status'], 'nominations_closed')
        log = SlatingActivity.objects.get(period=self.unlocked, action='period_status_changed')
        self.assertEqual(log.metadata, {'old_status': 'setup', 'new_status': 'nominations_open'})

    def test_refusals(self):
        self.assertEqual(self.status('bogus').json()['error'], 'invalid_status')
        r = self.status('setup')
        self.assertEqual((r.status_code, r.json()['error']), (409, 'unchanged'))
        self.unlocked.status = 'deliberation'
        self.unlocked.save()
        r = self.status('voting_open')
        self.assertEqual((r.status_code, r.json()['error']), (409, 'use_chair'))
        self.assertEqual(SlatingPeriod.objects.get(id=self.unlocked.id).status, 'deliberation')

    def test_pause_voting(self):
        r = self.status('deliberation', period=self.period, user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Voting paused. You can reopen it from Run the vote.')
        self.assertEqual(SlatingPeriod.objects.get(id=self.period.id).status, 'deliberation')


# ── Positions ────────────────────────────────────────────────────────────────

class PositionTests(_SetupBase):

    def add(self, **kw):
        body = {'title': 'Treasurer', 'code': 'treas', 'description': 'Money', 'min_gpa': '3.0',
                'allow_abstain': 'on'}
        body.update(kw)
        return self.call('post', self.positions_path(), body, user=self.manager)

    def test_list_matches_the_web_page(self):
        self.vp.is_active = False
        self.vp.save()
        body = self.call('get', self.positions_path(), user=self.manager).json()
        web = Client()
        web.force_login(self.manager)
        ctx = web.get(reverse('slating_positions', args=[self.period.id])).context
        self.assertEqual([p['id'] for p in body['positions']], [p.id for p in ctx['positions']])
        self.assertEqual([p['id'] for p in body['inactive']], [p.id for p in ctx['inactive_positions']])
        self.assertEqual(len(body['roles']), ctx['roles'].count())

    def test_add(self):
        r = self.add()
        self.assertEqual(r.status_code, 201, r.content)
        pos = SlatingPosition.objects.get(id=r.json()['position_id'])
        self.assertEqual((pos.code, pos.title, float(pos.min_gpa), pos.allow_abstain), ('TREAS', 'Treasurer', 3.0, True))
        self.assertEqual(pos.display_order, 2)   # after President and VP
        self.assertEqual(r.json()['message'], 'Position "Treasurer" added.')
        self.assertTrue(SlatingActivity.objects.filter(action='position_added', metadata__position_id=pos.id).exists())
        r = self.add(title='Again')
        self.assertEqual((r.status_code, r.json()['error']), (409, 'duplicate_code'))
        self.assertEqual(r.json()['detail'], 'A position with code "TREAS" already exists.')
        self.assertEqual(self.add(code='T1').json()['error'], 'invalid_input')
        self.assertEqual(self.add(title=' ', code='NEW').json()['error'], 'invalid_input')
        self.assertEqual(self.add(code='NEW', min_gpa='9').json()['error'], 'invalid_input')
        self.assertEqual(self.add(code='SEC', allow_abstain='off').status_code, 201)
        self.assertFalse(SlatingPosition.objects.get(period=self.period, code='SEC').allow_abstain)

    def test_edit(self):
        role = Role.objects.create(name='Ritual chair', code='RIT_TEST')
        self.pres.role = role
        self.pres.requires_prior_experience = True
        self.pres.save()
        path = self.positions_path(rest=f'{self.pres.id}/')
        got = self.call('get', path, user=self.manager).json()['position']
        web = Client()
        web.force_login(self.manager)
        web_json = web.get(reverse('slating_edit_position', args=[self.period.id, self.pres.id])).json()
        self.assertEqual({k: got[k] for k in web_json}, web_json)
        r = self.call('post', path, {'title': 'Chapter President', 'code': 'NOPE', 'description': '',
                                     'min_gpa': '', 'allow_abstain': 'off', 'role': role.id,
                                     'requires_prior_experience': True}, user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.pres.refresh_from_db()
        self.assertEqual((self.pres.title, self.pres.code, self.pres.allow_abstain), ('Chapter President', 'P', False))
        self.assertEqual((self.pres.role, self.pres.requires_prior_experience, self.pres.min_gpa), (role, True, None))
        self.assertTrue(SlatingActivity.objects.filter(action='position_modified').exists())
        # As on the web, a field left out is off / cleared.
        self.call('post', path, {'title': 'Chapter President'}, user=self.manager)
        self.pres.refresh_from_db()
        self.assertEqual((self.pres.role, self.pres.requires_prior_experience, self.pres.allow_abstain),
                         (None, False, True))
        r = self.call('post', path, {'title': ''}, user=self.manager)
        self.assertEqual(r.json()['error'], 'invalid_input')
        other = SlatingPosition.objects.create(period=self.unlocked, title='Elsewhere', code='E')
        self.assertEqual(self.call('get', self.positions_path(rest=f'{other.id}/'), user=self.manager).status_code,
                         404)

    def test_delete_deactivates_and_admins_hard_delete(self):
        r = self.call('post', self.positions_path(rest=f'{self.vp.id}/delete/'), {'action': 'hard_delete'},
                      user=self.manager)
        self.assertEqual(r.json()['message'], 'Position "VP" deactivated.')   # not a site admin: deactivated
        self.vp.refresh_from_db()
        self.assertFalse(self.vp.is_active)
        self.assertEqual([p['id'] for p in r.json()['inactive']], [self.vp.id])
        # A site admin (in an unlocked period) removes it for good — not while a slate has it.
        pos = SlatingPosition.objects.create(period=self.unlocked, title='Sec', code='S')
        slate = Slate.objects.create(period=self.unlocked)
        sc = SlateCandidate.objects.create(slate=slate, position=pos, write_in_member=self.voter)
        path = self.positions_path(self.unlocked, f'{pos.id}/delete/')
        r = self.call('post', path, {'action': 'hard_delete'}, user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'has_slate'))
        sc.delete()
        r = self.call('post', path, {'action': 'hard_delete'}, user=self.admin)
        self.assertEqual(r.json()['message'], 'Position "Sec" permanently deleted.')
        self.assertFalse(SlatingPosition.objects.filter(id=pos.id).exists())

    def test_toggle(self):
        path = self.positions_path(rest=f'{self.vp.id}/toggle/')
        self.assertFalse(self.call('post', path, user=self.manager).json()['is_active'])
        self.assertTrue(self.call('post', path, user=self.manager).json()['is_active'])
        self.assertEqual(self.call('post', path, user=self.voter).status_code, 403)

    def test_reorder(self):
        other = SlatingPosition.objects.create(period=self.unlocked, title='Elsewhere', code='E', display_order=7)
        r = self.call('post', self.positions_path(rest='reorder/'),
                      {'position_order': [self.vp.id, self.pres.id, other.id]}, user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([p['id'] for p in r.json()['positions']], [self.vp.id, self.pres.id])
        other.refresh_from_db()
        self.assertEqual(other.display_order, 7)   # another period's position is left alone
        for bad in ({}, {'position_order': 'x'}, {'position_order': ['1']}):
            r = self.call('post', self.positions_path(rest='reorder/'), bad, user=self.manager)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_copy_defaults(self):
        r = self.call('post', self.positions_path(rest='defaults/'), user=self.manager)
        self.assertEqual(r.status_code, 200, r.content)
        added = r.json()['added']
        self.assertEqual(added, len(r.json()['default_positions']))
        self.assertEqual(r.json()['message'], f'Added {added} default positions.')
        self.assertNotIn('VPRM', SlatingPosition.objects.filter(period=self.period).values_list('code', flat=True))
        self.assertTrue(SlatingActivity.objects.filter(details=f'Added {added} default positions').exists())
        r = self.call('post', self.positions_path(rest='defaults/'), user=self.manager)
        self.assertEqual((r.json()['added'], r.json()['message']), (0, 'All default positions already exist.'))

    def test_web_views_unchanged(self):
        web = Client()
        web.force_login(self.manager)
        r = web.post(reverse('slating_add_position', args=[self.period.id]), {'title': 'X', 'code': 'p'})
        self.assertEqual([str(m) for m in get_messages(r.wsgi_request)], ['A position with code "P" already exists.'])
        web.post(reverse('slating_add_position', args=[self.period.id]), {'title': 'Sec', 'code': 'sec'})
        self.assertTrue(SlatingPosition.objects.filter(period=self.period, code='SEC').exists())
        r = web.post(reverse('slating_api_reorder_positions', args=[self.period.id]),
                     f'{{"position_order": [{self.vp.id}, {self.pres.id}]}}', content_type='application/json')
        self.assertEqual(r.json(), {'status': 'success'})
        self.vp.refresh_from_db()
        self.assertEqual(self.vp.display_order, 0)
        r = web.post(reverse('slating_api_toggle_position', args=[self.period.id, self.vp.id]))
        self.assertEqual(r.json(), {'status': 'success', 'is_active': False})


# ── Transferring the committee's admin ──────────────────────────────────────

class TransferAdminTests(_SetupBase):

    def setUp(self):
        super().setUp()
        self.president = member('ss-pres')
        self.committee.admin = self.president
        self.committee.save()
        self.path = f'slating/periods/{self.period.id}/transfer-admin/'

    def test_who_may(self):
        for user in (self.voter, self.manager):   # even the slating manager: the page's own rule
            r = self.call('get', self.path, user=user)
            self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        body = self.call('get', self.path, user=self.president).json()
        self.assertEqual(body['current_admin']['user_id'], self.president.user_id)
        self.assertNotIn(self.president.user_id, [m['user_id'] for m in body['eligible']])
        # A site admin may, locked period or not (as on the web).
        self.assertEqual(self.call('get', self.path, user=self.admin).status_code, 200)
        bare = SlatingPeriod.objects.create(name='Bare', academic_term='2020')
        r = self.call('get', f'slating/periods/{bare.id}/transfer-admin/', user=self.admin)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'no_committee'))

    def test_validation(self):
        r = self.call('post', self.path, {'new_admin': self.voter.user_id, 'reason': ' '}, user=self.president)
        self.assertEqual(r.json()['detail'], 'Please provide a reason for the transfer.')
        r = self.call('post', self.path, {'reason': 'Running'}, user=self.president)
        self.assertEqual(r.json()['detail'], 'Please select a new admin.')
        r = self.call('post', self.path, {'new_admin': self.president.user_id, 'reason': 'Running'}, user=self.president)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'same_admin'))
        self.assertEqual(self.call('post', self.path, {'new_admin': 'nobody', 'reason': 'R'},
                                   user=self.president).status_code, 404)
        self.assertFalse(SlatingActivity.objects.filter(action='admin_transferred').exists())

    def test_transfer(self):
        r = self.call('post', self.path, {'new_admin': self.voter.user_id, 'reason': 'Running for President'},
                      user=self.president)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], f'Admin successfully transferred to {self.voter.name}.')
        self.assertFalse(r.json()['can_manage'])   # he handed it on
        self.committee.refresh_from_db()
        self.assertEqual(self.committee.admin, self.voter)
        p = SlatingPeriod.objects.get(id=self.period.id)
        self.assertTrue(p.admin_transferred)
        self.assertEqual((p.admin_transferred_from, p.admin_transfer_reason), (self.president, 'Running for President'))
        log = SlatingActivity.objects.get(action='admin_transferred')
        self.assertEqual(log.metadata['new_admin_id'], self.voter.user_id)
        self.assertEqual(r.json()['previous']['from'], self.president.name)
        # The new admin now manages the period.
        self.assertEqual(self.call('get', self.setup_path(), user=self.voter).status_code, 200)

    def test_web_view_unchanged(self):
        web = Client()
        web.force_login(self.voter)
        self.assertEqual(web.get(reverse('slating_transfer_admin', args=[self.period.id])).status_code, 403)
        web.force_login(self.president)
        url = reverse('slating_transfer_admin', args=[self.period.id])
        # The picker posts the member's user id (it posted a blank ``member.id`` before 11-04-26).
        self.assertContains(web.get(url), f'<option value="{self.voter.user_id}">')
        r = web.post(url, {'new_admin': self.president.user_id, 'reason': 'x'})
        self.assertEqual([(m.level_tag, str(m)) for m in get_messages(r.wsgi_request)],
                         [('warning', 'The new admin cannot be the same as the current admin.')])
        r = web.post(url, {'new_admin': self.voter.user_id, 'reason': 'Running'})
        self.assertRedirects(r, reverse('slating_period_setup', args=[self.period.id]), fetch_redirect_response=False)
        self.committee.refresh_from_db()
        self.assertEqual(self.committee.admin, self.voter)
