"""
09-24-26 — Kai entry points follow `KaiMemberPermission`, and admins get nothing.

Mason: "The Kai dashboard uses similar logic [to the education bug] — make sure
the perms work there too. Don't add admins to be able to view the kai
dashboard, it's sensitive data."

The Kai pages themselves already gated on `kai_access` flags. What did not:

* **The ways in.** The "Review Dashboard" link on /kai/, the admin-bar nav
  link (`user.can_access_kai`) and the Kai section on the committee pages
  used `Committee.is_chair()` / `is_chair or user.is_admin`. A member granted
  `can_view_report_list` never saw any of them.
* **Two admin doors into case data.** `manage_kai_permissions` accepted
  `is_admin` (an admin could grant themselves the identity flags — the exact
  edge v3.16.2 closed in /admin/), and `_can_appoint_standins` accepted
  `is_admin` without ever consulting `_get_kai_access`. Both are now real
  Kai chairs only.

Run with: python manage.py test src.tests.kai.test_kai_permission_entrypoints
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, KaiMemberPermission, ParliamentUser
from src.view.kai_reports import _can_appoint_standins


def make_user(uid, member_type='Member', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('kai-entry-pass-12345!')
    user.save()
    return user


class KaiEntryPointTests(TestCase):
    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_active=True, is_kai_committee=True)
        self.chair = make_user('ke-chair', member_type='Officer')
        self.committee.chairs.add(self.chair)
        self.grantee = make_user('ke-grantee')
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.grantee, can_view_report_list=True,
        )
        self.committee.members.add(self.grantee)
        self.admin = make_user('ke-admin', member_type='Officer', is_admin=True)
        self.nobody = make_user('ke-nobody')

    def _client(self, user):
        c = Client(); c.force_login(user); return c

    # ── the ways in ────────────────────────────────────────────────────────
    def test_a_grantee_sees_the_review_dashboard_link_on_their_kai_page(self):
        html = self._client(self.grantee).get(reverse('user_kai_dashboard')).content.decode()
        self.assertIn(reverse('view_kai_reports'), html)

    def test_a_grantee_can_access_kai(self):
        self.assertTrue(self.grantee.can_access_kai)

    def test_a_grantee_sees_the_kai_section_on_the_committee_page(self):
        r = self._client(self.grantee).get(reverse('committee_home', args=[self.committee.code]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(reverse('view_kai_reports'), r.content.decode())

    def test_a_grantee_does_not_see_the_member_perms_link(self):
        r = self._client(self.grantee).get(reverse('committee_home', args=[self.committee.code]))
        self.assertNotIn(reverse('manage_kai_permissions', args=[self.committee.code]), r.content.decode())

    def test_a_member_with_no_grant_sees_no_link(self):
        html = self._client(self.nobody).get(reverse('user_kai_dashboard')).content.decode()
        self.assertNotIn(reverse('view_kai_reports'), html)
        self.assertFalse(self.nobody.can_access_kai)

    # ── admins get nothing ─────────────────────────────────────────────────
    def test_an_admin_without_a_grant_sees_no_link_and_cannot_access_kai(self):
        html = self._client(self.admin).get(reverse('user_kai_dashboard')).content.decode()
        self.assertNotIn(reverse('view_kai_reports'), html)
        self.assertFalse(self.admin.can_access_kai)

    def test_an_admin_cannot_open_kai_member_permissions(self):
        r = self._client(self.admin).get(reverse('manage_kai_permissions', args=[self.committee.code]))
        self.assertEqual(r.status_code, 302)

    def test_an_admin_cannot_grant_themselves_kai_permissions(self):
        self._client(self.admin).post(
            reverse('update_kai_member_permission', args=[self.committee.code, self.admin.pk]),
            {'can_view_report_list': 'true', 'can_view_report_details': 'true',
             'can_view_submitter_identity': 'true', 'can_view_accused_identity': 'true'},
        )
        self.assertFalse(KaiMemberPermission.objects.filter(user=self.admin).exists())

    def test_an_admin_cannot_appoint_stand_ins(self):
        self.assertFalse(_can_appoint_standins(self.admin, self.committee))

    def test_the_chair_still_can(self):
        self.assertTrue(_can_appoint_standins(self.chair, self.committee))
        r = self._client(self.chair).get(reverse('manage_kai_permissions', args=[self.committee.code]))
        self.assertEqual(r.status_code, 200)

    def test_an_exec_board_flag_does_not_make_a_member_a_permissions_manager(self):
        """`Committee.is_chair()` treats every member of an is_exec_board
        committee as a chair; the Kai permissions page must not."""
        self.committee.is_exec_board = True
        self.committee.save()
        r = self._client(self.grantee).get(reverse('manage_kai_permissions', args=[self.committee.code]))
        self.assertEqual(r.status_code, 302)
