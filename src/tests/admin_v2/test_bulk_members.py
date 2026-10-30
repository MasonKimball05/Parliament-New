"""v4.1.0 admin-v2 bulk member changes: preview, apply, refusals."""
from django.urls import reverse

from src.models import ActivityLog
from src.models.users import Role
from src.tests.admin_v2.base import AdminV2TestCase, make_user

URL = 'admin_v2_bulk_members'


class BulkMembersTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.a = make_user('bulk-a', pledge_class='Fall 2022')
        self.b = make_user('bulk-b', pledge_class='Fall 2022')
        self.p = make_user('bulk-p', member_type='Pledge')
        self.role = Role.objects.create(name='Historian', code='HIST')
        self.solo = Role.objects.create(name='Treasurer X', code='TRX', one_per_chapter=True)

    def post(self, step, action, ids, **values):
        data = {'step': step, 'bulk_action': action, 'ids': ids}
        data.update(values)
        return self.client.post(reverse(URL), data)

    def test_list_filters_by_pledge_class(self):
        r = self.client.get(reverse(URL), {'pledge_class': 'Fall 2022'})
        self.assertEqual({m.user_id for m in r.context['members']}, {'bulk-a', 'bulk-b'})
        self.assertNotContains(r, 'onclick=')

    def test_preview_changes_nothing_and_apply_does(self):
        r = self.post('preview', 'set_status', ['bulk-a', 'bulk-b'], value_status='Alumni')
        self.assertEqual(r.context['will_change'], 2)
        self.a.refresh_from_db()
        self.assertEqual(self.a.member_status, 'Active')
        self.post('apply', 'set_status', ['bulk-a', 'bulk-b'], value='Alumni')
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual((self.a.member_status, self.b.member_status), ('Alumni', 'Alumni'))
        self.assertEqual(ActivityLog.objects.filter(description__contains='(bulk)').count(), 2)

    def test_own_account_is_skipped(self):
        r = self.post('preview', 'set_status', ['bulk-a', self.admin.user_id], value_status='Alumni')
        skips = {row['member'].user_id: row['skip'] for row in r.context['rows']}
        self.assertEqual(skips[self.admin.user_id], 'your own account')
        self.post('apply', 'set_status', ['bulk-a', self.admin.user_id], value='Alumni')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.member_status, 'Active')

    def test_pledges_go_through_initiation(self):
        self.post('apply', 'set_type', ['bulk-p'], value='Member')
        self.p.refresh_from_db()
        self.assertEqual(self.p.member_type, 'Pledge')

    def test_roles_add_remove_and_clear(self):
        self.post('apply', 'add_role', ['bulk-a', 'bulk-b'], value=str(self.role.pk))
        self.assertTrue(self.a.roles.filter(pk=self.role.pk).exists())
        self.post('apply', 'remove_role', ['bulk-a'], value=str(self.role.pk))
        self.assertFalse(self.a.roles.filter(pk=self.role.pk).exists())
        self.post('apply', 'clear_roles', ['bulk-b'])
        self.assertFalse(self.b.roles.exists())

    def test_clear_roles_resets_kai_permissions_like_remove(self):
        # 10-28-26: roles.clear() sends pk_set=None, so the Kai reset never ran and
        # an outgoing Kai chair's grants survived "Remove all roles".
        from src.models import Committee, KaiMemberPermission
        # The Kai committee is tied to an Exec Board role (the reset only runs for those).
        kai_role, _ = Role.objects.get_or_create(code='VPRM', defaults={'name': 'VP Risk Management'})
        kai = Committee.objects.create(name='Kai X', code='KAIX', is_active=True,
                                       is_kai_committee=True, role=kai_role)
        self.a.roles.add(kai_role)
        KaiMemberPermission.objects.create(committee=kai, user=self.b, can_view_report_list=True)
        self.post('apply', 'clear_roles', ['bulk-a'])
        self.assertFalse(self.a.roles.exists())
        self.assertFalse(KaiMemberPermission.objects.filter(committee=kai).exists())

    def test_one_per_chapter_role(self):
        r = self.post('preview', 'add_role', ['bulk-a', 'bulk-b'], value_role=str(self.solo.pk))
        self.assertEqual(r.context['will_change'], 0)
        self.post('apply', 'add_role', ['bulk-a'], value=str(self.solo.pk))
        self.assertTrue(self.a.roles.filter(pk=self.solo.pk).exists())
        # Held by bulk-a now: nobody else can get it in bulk.
        self.post('apply', 'add_role', ['bulk-b'], value=str(self.solo.pk))
        self.assertFalse(self.b.roles.filter(pk=self.solo.pk).exists())

    def test_member_route_does_not_swallow_bulk(self):
        self.assertEqual(self.client.get(reverse(URL)).templates[0].name, 'admin_v2/bulk_members.html')
