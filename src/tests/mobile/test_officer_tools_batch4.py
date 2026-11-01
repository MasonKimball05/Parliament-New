"""
Officer tools in the app, batch 4 (v4.x): Manage Roles and Role Transitions,
each behind its website page's own gate and through the web views' own
helpers (src/view/officer/manage_roles.py, src/view/officer/transitions.py).
Archived events are covered by test_officer_events.

    python manage.py test src.tests.mobile.test_officer_tools_batch4
"""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from src.models import (
    ActivityLog, Notification, Role, RoleHistory, TransitionChecklistItem, TransitionChecklistStatus,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.utils.semester import transition_semesters

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.officer = make_user('b4-off', member_type='Officer')
        self.member = make_user('b4-mem')
        self.advisor = make_user('b4-adv', member_type='Advisor')
        self.chair = make_user('b4-chair', member_type='Chair')
        self.admin = make_user('b4-admin', member_type='Member', is_admin=True)
        self.tokens = {u.user_id: make_device(u)[1]
                       for u in (self.officer, self.member, self.advisor, self.chair, self.admin)}
        self.role = Role.objects.create(name='B4 Historian', code='B4HIST', one_per_chapter=True)
        self.admin_role = Role.objects.create(name='B4 Chief', code='B4CHIEF', one_per_chapter=True,
                                              grants_admin=True)

    def call(self, user, method, path, body=None):
        fn = getattr(self.api, method)
        kw = bearer(self.tokens[user.user_id])
        if body is None:
            return fn(API + path, **kw)
        return fn(API + path, json.dumps(body), content_type='application/json', **kw)

    def last_log(self, action):
        return ActivityLog.objects.filter(metadata__action=action).latest('pk')


# ── Hub ──────────────────────────────────────────────────────────────────────

class HubFlagTests(_Base):

    def test_flags(self):
        d = self.call(self.officer, 'get', 'officer/').json()
        self.assertTrue(d['can_manage_roles'])
        self.assertTrue(d['can_transfer_roles'])
        self.assertFalse(d['can_view_archived_events'])
        self.assertIn('can_manage_resolutions', d)   # older keys stay
        self.assertTrue(self.call(self.admin, 'get', 'officer/').json()['can_view_archived_events'])
        self.assertTrue(self.call(self.chair, 'get', 'officer/').json()['can_manage_roles'])


# ── Manage roles ─────────────────────────────────────────────────────────────

class ManageRolesTests(_Base):

    def test_who_may_open(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.admin, 200),
                           (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.call(user, 'get', 'officer/roles/').status_code, code, user.user_id)
            self.assertEqual(self.call(user, 'get', f'officer/roles/{self.role.id}/').status_code, code)

    def test_list_shape_and_caller_flags(self):
        self.member.roles.add(self.role)
        d = self.call(self.officer, 'get', 'officer/roles/').json()
        row = next(r for r in d['roles'] if r['id'] == self.role.id)
        self.assertEqual(row['holders'], [{'user_id': 'b4-mem', 'name': 'User b4-mem'}])
        self.assertEqual(row['holder_count'], 1)
        self.assertEqual((d['can_edit'], d['can_grant_admin'], d['can_change_holders']), (True, False, True))
        d = self.call(self.chair, 'get', 'officer/roles/').json()
        self.assertEqual((d['can_edit'], d['can_grant_admin'], d['can_change_holders']), (False, False, False))

    def test_create_validates_and_logs(self):
        r = self.call(self.officer, 'post', 'officer/roles/', {'name': '', 'code': 'X'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        r = self.call(self.officer, 'post', 'officer/roles/', {'name': 'B4 Historian', 'code': 'NEW'})
        self.assertEqual(r.json()['detail'], 'A role with this name already exists.')
        r = self.call(self.officer, 'post', 'officer/roles/', {'name': 'Other', 'code': 'b4hist'})
        self.assertEqual(r.json()['detail'], 'A role with this code already exists.')
        r = self.call(self.officer, 'post', 'officer/roles/', {'name': 'Other', 'code': 'X' * 30})
        self.assertEqual(r.status_code, 400)
        r = self.call(self.officer, 'post', 'officer/roles/',
                      {'name': 'B4 Scribe', 'code': 'scribe', 'grants_admin': True})
        self.assertEqual(r.status_code, 201)
        role = Role.objects.get(name='B4 Scribe')
        self.assertEqual(role.code, 'SCRIBE')
        self.assertFalse(role.grants_admin)          # only admins may
        self.assertEqual(self.last_log('add_role').metadata['role_id'], role.id)

    def test_chair_cannot_create_edit_or_delete(self):
        r = self.call(self.chair, 'post', 'officer/roles/', {'name': 'Chair role', 'code': 'CR'})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        r = self.call(self.chair, 'patch', f'officer/roles/{self.role.id}/', {'code': 'CNB'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.call(self.chair, 'delete', f'officer/roles/{self.role.id}/').status_code, 403)
        self.role.refresh_from_db()
        self.assertEqual(self.role.code, 'B4HIST')

    def test_edit(self):
        r = self.call(self.officer, 'patch', f'officer/roles/{self.role.id}/',
                      {'name': 'B4 Keeper', 'code': 'keep', 'grants_admin': True, 'one_per_chapter': False})
        self.assertEqual(r.status_code, 200)
        self.role.refresh_from_db()
        self.assertEqual((self.role.name, self.role.code, self.role.one_per_chapter, self.role.grants_admin),
                         ('B4 Keeper', 'KEEP', False, False))
        self.assertEqual(len(r.json()['changes']), 3)
        self.assertEqual(self.last_log('edit_role').metadata['role_id'], self.role.id)
        r = self.call(self.officer, 'patch', f'officer/roles/{self.role.id}/', {'code': 'b4chief'})
        self.assertEqual(r.json()['detail'], 'A role with this code already exists.')
        r = self.call(self.officer, 'patch', f'officer/roles/{self.role.id}/', {'name': '  '})
        self.assertEqual(r.status_code, 400)
        r = self.call(self.admin, 'patch', f'officer/roles/{self.role.id}/', {'grants_admin': True})
        self.role.refresh_from_db()
        self.assertTrue(self.role.grants_admin)

    def test_delete_refused_while_held(self):
        self.member.roles.add(self.role)
        r = self.call(self.officer, 'delete', f'officer/roles/{self.role.id}/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('currently assigned to 1 member', r.json()['detail'])
        self.member.roles.remove(self.role)
        r = self.call(self.officer, 'delete', f'officer/roles/{self.role.id}/')
        self.assertEqual(r.json(), {'ok': True, 'name': 'B4 Historian'})
        self.assertFalse(Role.objects.filter(pk=self.role.pk).exists())
        self.assertEqual(self.last_log('delete_role').metadata['role_code'], 'B4HIST')

    def test_assign_and_unassign(self):
        path = f'officer/roles/{self.role.id}/members/'
        ids = [m['user_id'] for m in self.call(self.officer, 'get', path).json()['members']]
        self.assertIn('b4-mem', ids)
        r = self.call(self.officer, 'post', path, {'user_id': 'b4-mem'})
        self.assertEqual(r.json()['role']['holders'][0]['user_id'], 'b4-mem')
        self.assertTrue(self.member.roles.filter(pk=self.role.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(description__contains='assigned User b4-mem to role').exists())
        # One holder only for an exclusive role.
        r = self.call(self.officer, 'post', path, {'user_id': 'b4-adv'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertEqual(self.call(self.officer, 'post', path, {}).status_code, 400)
        self.assertEqual(self.call(self.officer, 'post', path, {'user_id': 'nobody'}).status_code, 404)
        r = self.call(self.officer, 'delete', f'{path}b4-mem/')
        self.assertEqual(r.json()['role']['holders'], [])
        self.assertFalse(self.member.roles.filter(pk=self.role.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(description__contains='removed User b4-mem from role').exists())

    def test_role_change_rules(self):
        path = f'officer/roles/{self.role.id}/members/'
        self.assertEqual(self.call(self.chair, 'post', path, {'user_id': 'b4-mem'}).status_code, 403)
        # Only an admin touches an admin's roles, or a role that grants admin.
        self.assertEqual(self.call(self.officer, 'post', path, {'user_id': 'b4-admin'}).status_code, 403)
        r = self.call(self.officer, 'post', f'officer/roles/{self.admin_role.id}/members/', {'user_id': 'b4-mem'})
        self.assertEqual(r.status_code, 403)
        r = self.call(self.admin, 'post', f'officer/roles/{self.admin_role.id}/members/', {'user_id': 'b4-mem'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.member.roles.filter(pk=self.role.pk).exists())


# ── Role transitions ─────────────────────────────────────────────────────────

class TransitionTests(_Base):

    def setUp(self):
        super().setUp()
        self.old = make_user('b4-old', member_type='Chair')
        self.new = make_user('b4-new')
        self.old.roles.add(self.role)
        self.end, self.start = transition_semesters()
        self.history = RoleHistory.objects.create(user=self.old, role_name=self.role.name,
                                                  start_semester='Fall 2020')
        self.item = TransitionChecklistItem.objects.create(role=self.role, text='Hand over the albums')

    def transfer(self, user=None, **body):
        body.setdefault('incoming_user_id', 'b4-new')
        return self.call(user or self.officer, 'post', f'officer/transitions/{self.role.id}/transfer/', body)

    def test_who_may_open(self):
        for user, code in ((self.officer, 200), (self.chair, 200), (self.admin, 200),
                           (self.advisor, 403), (self.member, 403)):
            self.assertEqual(self.call(user, 'get', 'officer/transitions/').status_code, code, user.user_id)

    def test_list(self):
        d = self.call(self.officer, 'get', 'officer/transitions/').json()
        row = next(r for r in d['roles'] if r['id'] == self.role.id)
        holder = row['holders'][0]
        self.assertEqual(holder['user_id'], 'b4-old')
        self.assertEqual(holder['checklist_web_path'], f'/officers/transitions/checklist/{self.history.id}/')
        self.assertTrue(row['can_transfer'])
        chief = next(r for r in d['roles'] if r['id'] == self.admin_role.id)
        self.assertFalse(chief['can_transfer'])
        self.assertIn('b4-new', [m['user_id'] for m in d['members']])
        self.assertEqual(d['semesters'], {'ending': self.end, 'starting': self.start})
        self.assertTrue(d['can_transfer'])
        self.assertFalse(self.call(self.chair, 'get', 'officer/transitions/').json()['can_transfer'])

    def test_dry_run_changes_nothing(self):
        before = ActivityLog.objects.count()
        r = self.transfer(outgoing_user_id='b4-old', incoming_type='Chair', demote_outgoing=True, dry_run=True)
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['dry_run'])
        self.assertIsNone(d['role_history_id'])
        self.assertEqual(d['changes'], [
            'removed B4 Historian from User b4-old',
            'demoted User b4-old to Member',
            'assigned B4 Historian to User b4-new',
            f'opened role history ({self.start})',
            'updated User b4-new member type to Chair',
        ])
        self.old.refresh_from_db()
        self.new.refresh_from_db()
        self.assertTrue(self.old.roles.filter(pk=self.role.pk).exists())
        self.assertEqual((self.old.member_type, self.new.member_type), ('Chair', 'Member'))
        self.assertFalse(self.new.roles.exists())
        self.history.refresh_from_db()
        self.assertEqual(self.history.end_semester, '')
        self.assertFalse(RoleHistory.objects.filter(user=self.new).exists())
        self.assertFalse(TransitionChecklistStatus.objects.exists())
        self.assertEqual(ActivityLog.objects.count(), before)

    def test_transfer(self):
        preview = self.transfer(outgoing_user_id='b4-old', demote_outgoing=True, dry_run=True).json()['changes']
        d = self.transfer(outgoing_user_id='b4-old', demote_outgoing=True).json()
        self.assertFalse(d['dry_run'])
        self.assertEqual(d['changes'], preview)
        self.old.refresh_from_db()
        self.assertFalse(self.old.roles.filter(pk=self.role.pk).exists())
        self.assertEqual(self.old.member_type, 'Member')
        self.assertTrue(self.new.roles.filter(pk=self.role.pk).exists())
        self.history.refresh_from_db()
        self.assertEqual(self.history.end_semester, self.end)
        opened = RoleHistory.objects.get(user=self.new, role_name=self.role.name)
        self.assertEqual((opened.start_semester, opened.end_semester), (self.start, ''))
        self.assertEqual(d['role_history_id'], opened.id)
        self.assertEqual(d['checklist_web_path'], f'/officers/transitions/checklist/{opened.id}/')
        self.assertTrue(TransitionChecklistStatus.objects.filter(role_history=opened, item=self.item).exists())
        log = self.last_log('transfer_role')
        self.assertEqual((log.metadata['incoming_user_id'], log.metadata['outgoing_user_id']), ('b4-new', 'b4-old'))
        self.assertEqual(log.user, self.officer)

    def test_exclusive_role_clears_holders_when_none_named(self):
        d = self.transfer().json()
        self.assertIn('removed B4 Historian from User b4-old', d['changes'])
        self.assertFalse(self.old.roles.filter(pk=self.role.pk).exists())
        self.history.refresh_from_db()
        self.assertEqual(self.history.end_semester, self.end)

    def test_grants_admin_role_is_admins_only(self):
        path = f'officer/transitions/{self.admin_role.id}/transfer/'
        r = self.call(self.officer, 'post', path, {'incoming_user_id': 'b4-new', 'dry_run': True})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        r = self.call(self.admin, 'post', path, {'incoming_user_id': 'b4-new', 'incoming_type': 'Officer'})
        self.assertIn('granted admin to User b4-new', r.json()['changes'])
        self.new.refresh_from_db()
        self.assertTrue(self.new.is_admin)
        self.assertEqual(self.new.member_type, 'Officer')

    def test_refusals(self):
        r = self.transfer(user=self.chair, dry_run=True)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'))
        self.assertEqual(self.transfer(incoming_user_id='b4-admin').status_code, 403)
        self.assertTrue(self.old.roles.filter(pk=self.role.pk).exists())

    def test_validation(self):
        for body in ({'incoming_user_id': ''}, {'incoming_type': 'Advisor'},
                     {'outgoing_user_id': 'b4-mem'}, {'incoming_user_id': 5}):
            r = self.transfer(**body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)
        self.assertEqual(self.transfer(incoming_user_id='nobody').status_code, 404)
        self.new.member_status = 'Alumni'
        self.new.save()
        self.assertEqual(self.transfer().status_code, 404)
        r = self.call(self.officer, 'post', 'officer/transitions/999999/transfer/', {'incoming_user_id': 'b4-new'})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(ActivityLog.objects.filter(metadata__action='transfer_role').exists())

    def test_points_incoming_at_the_knowledge_base_only_for_real(self):
        from src.models import RoleKnowledgeBase, RoleKnowledgeBaseRevision
        kb = RoleKnowledgeBase.objects.create(role=self.role)
        RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='Notes from the last historian.')
        self.transfer(outgoing_user_id='b4-old', dry_run=True)
        self.assertFalse(Notification.objects.filter(recipient=self.new, notification_type='role_kb_available').exists())
        self.transfer(outgoing_user_id='b4-old')
        self.assertTrue(Notification.objects.filter(recipient=self.new, notification_type='role_kb_available').exists())
