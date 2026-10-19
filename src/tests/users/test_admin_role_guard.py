"""
v3.50.1 — only an admin may give or take a role that makes its holder an admin
(`Role.grants_admin`), or change the roles on an admin's account.

Before: `officer_required` admits chairs, and `transfer_role` sets `is_admin`
on the incoming holder of a grants_admin role. So any chair could POST the
President role to himself and be an admin, and take the role from the real
President in the same request. `edit_member`, `assign_role_member`,
`unassign_role_member`, `add_member` and `assign_appointment` let a non-admin
put an admin role on an account, which "Sync officer admins" then turns into
an admin. Rule: `src.permissions.role_change_error`.

Run with: python manage.py test src.tests.users.test_admin_role_guard
"""
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from src.models import ParliamentUser, Role


def _user(uid, member_type='Member', is_admin=False):
    u = ParliamentUser.objects.create_user(user_id=uid, name=f'User {uid}', username=f'u{uid}',
                                           member_type=member_type)
    u.member_status = 'Active'
    u.is_admin = is_admin
    u.set_password('role-guard-pass-12345!')
    u.save()
    return u


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class AdminRoleGuardTests(TestCase):
    def setUp(self):
        self.president_role = Role.objects.create(name='President', code='President',
                                                  one_per_chapter=True, grants_admin=True)
        self.chair_role = Role.objects.create(name='Social Chair', code='SocialChair')
        self.president = _user('9100', 'Officer', is_admin=True)
        self.president.roles.add(self.president_role)
        self.chair = _user('9101', 'Chair')
        self.officer = _user('9102', 'Officer')
        self.member = _user('9103')

    def post_json(self, user, url, data):
        self.client.force_login(user)
        return self.client.post(url, data=json.dumps(data), content_type='application/json')

    def transfer(self, user, role, incoming, **extra):
        return self.post_json(user, reverse('transfer_role', args=[role.id]),
                              {'incoming_user_id': incoming.user_id, **extra})

    # transfer_role ---------------------------------------------------------

    def test_chair_cannot_transfer_the_admin_role_to_himself(self):
        r = self.transfer(self.chair, self.president_role, self.chair, incoming_type='Officer')
        self.assertEqual(r.status_code, 403)
        self.chair.refresh_from_db()
        self.assertFalse(self.chair.is_admin)
        self.assertEqual(self.chair.member_type, 'Chair')
        self.assertTrue(self.president.roles.filter(pk=self.president_role.pk).exists())

    def test_officer_cannot_transfer_the_admin_role_either(self):
        r = self.transfer(self.officer, self.president_role, self.member)
        self.assertEqual(r.status_code, 403)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_admin)

    def test_admin_can_transfer_the_admin_role(self):
        r = self.transfer(self.president, self.president_role, self.member)
        self.assertEqual(r.status_code, 200, r.content)
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_admin)

    def test_officer_can_still_transfer_an_ordinary_role(self):
        self.member.roles.add(self.chair_role)
        r = self.transfer(self.officer, self.chair_role, self.chair, outgoing_user_id=self.member.user_id)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(self.chair.roles.filter(pk=self.chair_role.pk).exists())

    def test_officer_cannot_take_an_ordinary_role_from_an_admin(self):
        self.president.roles.add(self.chair_role)
        r = self.transfer(self.officer, self.chair_role, self.member,
                          outgoing_user_id=self.president.user_id)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.president.roles.filter(pk=self.chair_role.pk).exists())

    # assign / unassign -----------------------------------------------------

    def test_assign_and_unassign_admin_role_are_admin_only(self):
        r = self.post_json(self.chair, reverse('assign_role_member', args=[self.president_role.id]),
                           {'user_id': self.chair.user_id})
        self.assertEqual(r.status_code, 403)
        r = self.post_json(self.officer, reverse('unassign_role_member', args=[self.president_role.id]),
                           {'user_id': self.president.user_id})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.chair.roles.filter(pk=self.president_role.pk).exists())
        self.assertTrue(self.president.roles.filter(pk=self.president_role.pk).exists())

    def test_assign_ordinary_role_still_works(self):
        r = self.post_json(self.officer, reverse('assign_role_member', args=[self.chair_role.id]),
                           {'user_id': self.member.user_id})
        self.assertEqual(r.status_code, 200, r.content)

    # edit_member / add_member ----------------------------------------------

    def test_edit_member_cannot_add_admin_role_even_to_self(self):
        r = self.post_json(self.chair, reverse('edit_member', args=[self.chair.user_id]),
                           {'name': self.chair.name, 'roles': [self.president_role.id]})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.chair.roles.filter(pk=self.president_role.pk).exists())

    def test_edit_member_ordinary_role_and_bad_roles(self):
        r = self.post_json(self.officer, reverse('edit_member', args=[self.member.user_id]),
                           {'roles': [self.chair_role.id]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(self.member.roles.filter(pk=self.chair_role.pk).exists())
        r = self.post_json(self.officer, reverse('edit_member', args=[self.member.user_id]),
                           {'roles': ['x']})
        self.assertEqual(r.status_code, 400)

    def test_admin_can_add_admin_role_in_edit_member(self):
        r = self.post_json(self.president, reverse('edit_member', args=[self.member.user_id]),
                           {'roles': [self.president_role.id]})
        self.assertEqual(r.status_code, 200, r.content)

    def test_add_member_with_admin_role_is_admin_only(self):
        data = {'user_id': '9200', 'name': 'New Guy', 'member_type': 'Member',
                'member_status': 'Active', 'roles': [self.president_role.id]}
        r = self.post_json(self.officer, reverse('add_member'), data)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(ParliamentUser.objects.filter(user_id='9200').exists())
