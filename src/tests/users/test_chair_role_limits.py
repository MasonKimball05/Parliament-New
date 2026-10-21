"""
v3.51.0 — chairs don't hand out roles or promote people; only officers (and
admins) do. Plus: an appointment's role is assigned only after a closed,
passed vote, and only once.

Mason, 10-19-26 (answering the 10-18 #needs-review): "only someone with
officers should be able to give others admin" — confirmed as: chairs lose role
assignment/transfer and can't change member types past Member (or their own);
officers keep it; admin-granting roles stay admin-only (v3.50.1).

`officer_required` admits chairs, so the rules live in
`src.permissions.role_change_error` / `member_type_change_error`.

Run with: python manage.py test src.tests.users.test_chair_role_limits
"""
import json

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import Legislation, ParliamentUser, Role


def _user(uid, member_type='Member', is_admin=False):
    u = ParliamentUser.objects.create_user(user_id=uid, name=f'User {uid}', username=f'u{uid}',
                                           member_type=member_type)
    u.member_status = 'Active'
    u.is_admin = is_admin
    u.save()
    return u


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class _Base(TestCase):
    def setUp(self):
        self.role = Role.objects.create(name='Social Chair', code='SocialChair')
        self.other_role = Role.objects.create(name='Rush Chair', code='RushChair')
        self.chair = _user('9301', 'Chair')
        self.officer = _user('9302', 'Officer')
        self.member = _user('9303')
        self.chair.roles.add(self.other_role)

    def post_json(self, user, url, data):
        self.client.force_login(user)
        return self.client.post(url, data=json.dumps(data), content_type='application/json')


class ChairRoleTests(_Base):
    def test_chair_cannot_assign_or_unassign_any_role(self):
        r = self.post_json(self.chair, reverse('assign_role_member', args=[self.role.id]),
                           {'user_id': self.member.user_id})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.member.roles.filter(pk=self.role.pk).exists())
        r = self.post_json(self.chair, reverse('assign_role_member', args=[self.role.id]),
                           {'user_id': self.chair.user_id})
        self.assertEqual(r.status_code, 403)
        r = self.post_json(self.chair, reverse('unassign_role_member', args=[self.other_role.id]),
                           {'user_id': self.chair.user_id})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.chair.roles.filter(pk=self.other_role.pk).exists())

    def test_chair_cannot_transfer_a_role(self):
        r = self.post_json(self.chair, reverse('transfer_role', args=[self.role.id]),
                           {'incoming_user_id': self.chair.user_id})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.chair.roles.filter(pk=self.role.pk).exists())

    def test_chair_cannot_change_roles_in_edit_member(self):
        r = self.post_json(self.chair, reverse('edit_member', args=[self.member.user_id]),
                           {'roles': [self.role.id]})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(self.member.roles.filter(pk=self.role.pk).exists())

    def test_chair_cannot_create_edit_or_delete_roles(self):
        r = self.post_json(self.chair, reverse('add_role'), {'name': 'New', 'code': 'NEW'})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Role.objects.filter(code='NEW').exists())
        r = self.post_json(self.chair, reverse('role_detail', args=[self.other_role.id]),
                           {'name': 'Rush Chair', 'code': 'CNB'})
        self.assertEqual(r.status_code, 403)
        self.other_role.refresh_from_db()
        self.assertEqual(self.other_role.code, 'RushChair')
        r = self.post_json(self.chair, reverse('delete_role', args=[self.role.id]), {})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Role.objects.filter(pk=self.role.pk).exists())

    def test_officer_still_manages_roles(self):
        r = self.post_json(self.officer, reverse('assign_role_member', args=[self.role.id]),
                           {'user_id': self.member.user_id})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.post_json(self.officer, reverse('add_role'), {'name': 'New', 'code': 'NEW'})
        self.assertEqual(r.status_code, 200, r.content)


class ChairMemberTypeTests(_Base):
    def test_chair_cannot_make_himself_an_officer(self):
        r = self.post_json(self.chair, reverse('edit_member', args=[self.chair.user_id]),
                           {'member_type': 'Officer'})
        self.assertEqual(r.status_code, 403)
        self.chair.refresh_from_db()
        self.assertEqual(self.chair.member_type, 'Chair')

    def test_chair_cannot_promote_a_member(self):
        for t in ('Officer', 'Chair', 'Advisor'):
            r = self.post_json(self.chair, reverse('edit_member', args=[self.member.user_id]),
                               {'member_type': t})
            self.assertEqual(r.status_code, 403, t)
        self.member.refresh_from_db()
        self.assertEqual(self.member.member_type, 'Member')

    def test_chair_can_still_edit_a_member_without_changing_type(self):
        r = self.post_json(self.chair, reverse('edit_member', args=[self.member.user_id]),
                           {'member_type': 'Member', 'preferred_name': 'Bob'})
        self.assertEqual(r.status_code, 200, r.content)

    def test_chair_cannot_add_an_officer(self):
        r = self.post_json(self.chair, reverse('add_member'),
                           {'user_id': '9399', 'name': 'New Guy', 'member_type': 'Officer', 'member_status': 'Active'})
        self.assertEqual(r.status_code, 403, r.content)
        self.assertFalse(ParliamentUser.objects.filter(user_id='9399').exists())

    def test_chair_bulk_import_refuses_elevated_rows(self):
        r = self.post_json(self.chair, reverse('bulk_import_members'),
                           [{'name': 'Imp One', 'username': 'impone', 'member_type': 'Officer'}])
        self.assertFalse(ParliamentUser.objects.filter(username='impone').exists(), r.content)

    def test_officer_can_promote(self):
        r = self.post_json(self.officer, reverse('edit_member', args=[self.member.user_id]),
                           {'member_type': 'Chair'})
        self.assertEqual(r.status_code, 200, r.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.member_type, 'Chair')


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class AssignAppointmentChecksTests(TestCase):
    def setUp(self):
        self.officer = _user('9401', 'Officer')
        self.nominee = _user('9402')
        self.role = Role.objects.create(name='Social Chair', code='SocialChair')
        self.leg = Legislation.objects.create(
            title='Appoint Social', description='Vote', posted_by=self.officer,
            legislation_type='appointment', appointment_role=self.role, appointment_member=self.nominee,
            available_at=timezone.now(), status='passed', passed=True, voting_closed=True,
        )
        self.client.force_login(self.officer)
        self.url = reverse('assign_appointment', kwargs={'legislation_id': self.leg.id})

    def assigned(self):
        return self.nominee.roles.filter(pk=self.role.pk).exists()

    def test_open_vote_is_refused(self):
        Legislation.objects.filter(pk=self.leg.pk).update(voting_closed=False, status='active')
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.post(self.url)
        self.assertFalse(self.assigned())

    def test_failed_vote_is_refused(self):
        Legislation.objects.filter(pk=self.leg.pk).update(passed=False, status='failed')
        self.client.post(self.url)
        self.assertFalse(self.assigned())

    def test_assigned_once(self):
        self.client.post(self.url)
        self.assertTrue(self.assigned())
        self.leg.refresh_from_db()
        self.assertTrue(self.leg.appointment_assigned)
        self.nominee.roles.remove(self.role)
        self.client.post(self.url)
        self.assertFalse(self.assigned())

    def test_chair_cannot_assign(self):
        self.client.force_login(_user('9403', 'Chair'))
        self.client.post(self.url)
        self.assertFalse(self.assigned())
