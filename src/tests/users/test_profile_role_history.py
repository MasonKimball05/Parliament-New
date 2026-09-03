"""
v3.29.6 — editing an existing role history entry from the profile page.

Requested by Mason: "in profile there is also no way to edit role history
only delete and add a new one."

`profile_view` already had `role_history_add_submit` and
`role_history_delete_submit` branches; there was no in-place update path,
so fixing a typo in a position name or a semester meant delete + re-add
(losing the original creation order/id). New `role_history_edit_submit`
branch scoped to `user=user` (same scoping as the delete branch — a member
can only edit their own entries) updates the existing row in place.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, RoleHistory


def make_user(uid='RH-U1', **kwargs):
    defaults = dict(name='RH User', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('rh-edit-test-12345!')
    user.save()
    return user


class RoleHistoryEditTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user('RH-U2')
        self.rh = RoleHistory.objects.create(
            user=self.user, role_name='Secretary', start_semester='Fall 2025', end_semester='',
        )
        self.client.login(username=self.user.username, password='rh-edit-test-12345!')

    def test_can_edit_in_place_without_delete_and_readd(self):
        before_id = self.rh.id
        before_count = RoleHistory.objects.filter(user=self.user).count()
        self.client.post(reverse('profile'), {
            'role_history_edit_submit': '1', 'rh_id': self.rh.id,
            'rh_role_name': 'Vice President', 'rh_start_semester': 'Spring 2026',
            'rh_end_semester': '',
        })
        self.rh.refresh_from_db()
        self.assertEqual(self.rh.id, before_id)
        self.assertEqual(self.rh.role_name, 'Vice President')
        self.assertEqual(self.rh.start_semester, 'Spring 2026')
        self.assertEqual(RoleHistory.objects.filter(user=self.user).count(), before_count)

    def test_can_clear_end_semester_back_to_current(self):
        self.rh.end_semester = 'Fall 2026'
        self.rh.save(update_fields=['end_semester'])
        self.client.post(reverse('profile'), {
            'role_history_edit_submit': '1', 'rh_id': self.rh.id,
            'rh_role_name': 'Secretary', 'rh_start_semester': 'Fall 2025',
            'rh_end_semester': '',
        })
        self.rh.refresh_from_db()
        self.assertEqual(self.rh.end_semester, '')

    def test_requires_role_name_and_start_semester(self):
        self.client.post(reverse('profile'), {
            'role_history_edit_submit': '1', 'rh_id': self.rh.id,
            'rh_role_name': '', 'rh_start_semester': '',
        })
        self.rh.refresh_from_db()
        self.assertEqual(self.rh.role_name, 'Secretary')  # unchanged

    def test_cannot_edit_another_members_entry(self):
        other_rh = RoleHistory.objects.create(user=self.other, role_name='Chair', start_semester='Fall 2025')
        self.client.post(reverse('profile'), {
            'role_history_edit_submit': '1', 'rh_id': other_rh.id,
            'rh_role_name': 'Hijacked', 'rh_start_semester': 'Spring 2026',
        })
        other_rh.refresh_from_db()
        self.assertEqual(other_rh.role_name, 'Chair')

    def test_ajax_returns_updated_json(self):
        response = self.client.post(
            reverse('profile'),
            {
                'role_history_edit_submit': '1', 'rh_id': self.rh.id,
                'rh_role_name': 'Treasurer', 'rh_start_semester': 'Spring 2026',
                'rh_end_semester': 'Fall 2026',
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['role_name'], 'Treasurer')
        self.assertEqual(data['start_semester'], 'Spring 2026')
        self.assertEqual(data['end_semester'], 'Fall 2026')
