"""
v3.41.0 — house map "N unassigned" quick-manage panel.

The count leaves out the 1800s original founders and Removed members; the
badge opens a panel only for house managers; set_member_big covers bigs and
littles with the house permission, refuses loops, and won't touch a pledge
whose big is on the education dashboard.

Run with: python manage.py test src.tests.users.test_house_map_unassigned
"""
from django.test import TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser, PledgeBigAssignment


def make_user(uid, member_type='Member', **kw):
    return ParliamentUser.objects.create(
        user_id=uid, username=uid, name=f'User {uid}',
        member_type=member_type, member_status=kw.pop('member_status', 'Active'),
        **kw)


class UnassignedCountTests(TestCase):
    def setUp(self):
        self.officer = make_user('off', member_type='Officer', house='Smith')
        self.member = make_user('mem', house='Smith')
        make_user('u1')  # unassigned, counts
        make_user('u2', member_status='Alumni')  # unassigned alum, counts
        make_user('of1', member_status='Alumni', pledge_class='Original Founders')
        make_user('gone', member_status='Removed')

    def test_count_skips_original_founders_and_removed(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('house_map'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['unassigned_count'], 2)

    def test_plain_member_gets_no_panel(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('house_map'))
        self.assertIsNone(resp.context['unassigned_panel'])
        self.assertNotContains(resp, 'js-open-unassigned')
        self.assertNotContains(resp, 'unassigned-data')

    def test_manager_gets_panel_without_founders(self):
        self.client.force_login(self.officer)
        resp = self.client.get(reverse('house_map'))
        self.assertContains(resp, 'js-open-unassigned')
        panel = resp.context['unassigned_panel']
        self.assertEqual(sorted(panel['unassigned']), ['u1', 'u2'])
        self.assertNotIn('gone', {p['id'] for p in panel['people']})


class SetMemberBigTests(TestCase):
    def setUp(self):
        self.officer = make_user('off', member_type='Officer')
        self.member = make_user('mem')
        self.big = make_user('big', house='Knox')
        self.little = make_user('lil')

    def _post(self, target, big):
        return self.client.post(
            reverse('set_member_big', args=[target.pk]), {'big': big})

    def test_needs_house_permission(self):
        self.client.force_login(self.member)
        self.assertEqual(self._post(self.little, self.big.pk).status_code, 403)
        self.little.refresh_from_db()
        self.assertIsNone(self.little.big_brother)

    def test_sets_big_and_inherits_house_then_clears(self):
        self.client.force_login(self.officer)
        resp = self._post(self.little, self.big.pk)
        self.assertEqual(resp.json(), {'big': 'big', 'house': 'Knox'})
        self.little.refresh_from_db()
        self.assertEqual((self.little.big_brother_id, self.little.house), ('big', 'Knox'))

        self._post(self.little, '')
        self.little.refresh_from_db()
        self.assertIsNone(self.little.big_brother)

    def test_refuses_loops(self):
        self.client.force_login(self.officer)
        self._post(self.little, self.big.pk)
        self.assertEqual(self._post(self.big, self.little.pk).status_code, 400)
        self.assertEqual(self._post(self.big, self.big.pk).status_code, 400)
        self.big.refresh_from_db()
        self.assertIsNone(self.big.big_brother)

    def test_refuses_education_managed_pledge(self):
        pledge = make_user('P-ABC123', member_type='Pledge')
        committee = Committee.objects.create(
            name='Education', code='EDUCATION', is_active=True,
            is_education_committee=True)
        PledgeBigAssignment.objects.create(
            committee=committee, pledge=pledge, big=self.big,
            created_by=self.officer)
        self.client.force_login(self.officer)
        self.assertEqual(self._post(pledge, self.member.pk).status_code, 409)
        pledge.refresh_from_db()
        self.assertIsNone(pledge.big_brother)
