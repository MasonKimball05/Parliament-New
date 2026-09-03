"""
v3.29.6 — editing a C&B resolution collaborator's role in place.

Requested by Mason: "on the cnb resolution builder, once you add someone as
a collaborator you cannot adjust their perms you have to remove them and
re-add them."

`cnb_add_collaborator` (src/view/officer/cnb.py) already handled this
correctly on the backend — `get_or_create` + an update branch when the
collaborator already exists — since whenever it was written. The gap was
purely in `templates/cnb/resolution_detail.html`: the collaborators list
rendered the role as static text with only a "Remove" action, so the
already-working update path was unreachable from the UI. Fixed by adding
an inline role `<select>` + Save form per row that posts to the SAME
`cnb_add_collaborator` endpoint with the existing user's id — no new view,
no new URL.
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, Resolution, ResolutionCollaborator


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('cnb-collab-test-12345!')
    user.save()
    return user


class CollaboratorRoleEditTests(TestCase):
    def setUp(self):
        self.cnb_holder = make_user('CNB-H1', is_admin=True)
        self.collaborator = make_user('CNB-C1')
        self.resolution = Resolution.objects.create(title='Test Resolution', created_by=self.cnb_holder)
        self.collab = ResolutionCollaborator.objects.create(
            resolution=self.resolution, user=self.collaborator, role='viewer', added_by=self.cnb_holder,
        )
        self.client.login(username=self.cnb_holder.username, password='cnb-collab-test-12345!')

    def test_can_change_role_without_removing_and_readding(self):
        """The core ask: adjust an existing collaborator's role in place."""
        before_count = ResolutionCollaborator.objects.filter(resolution=self.resolution).count()
        self.client.post(
            reverse('cnb_add_collaborator', args=[self.resolution.pk]),
            {'user_id': self.collaborator.pk, 'role': 'editor'},
        )
        self.collab.refresh_from_db()
        self.assertEqual(self.collab.role, 'editor')
        # Same row updated, not a duplicate created alongside the old one.
        self.assertEqual(
            ResolutionCollaborator.objects.filter(resolution=self.resolution).count(),
            before_count,
        )

    def test_detail_page_renders_inline_role_select_for_existing_collaborator(self):
        response = self.client.get(reverse('cnb_resolution_detail', args=[self.resolution.pk]))
        self.assertContains(response, 'value="viewer" selected')
        # The old "Remove and re-add" workflow (static role text, no select
        # in the row) should no longer be the only option.
        self.assertContains(response, f'value="{self.collaborator.pk}"')

    def test_non_cnb_member_cannot_change_role(self):
        outsider = make_user('CNB-O1')
        self.client.logout()
        self.client.login(username=outsider.username, password='cnb-collab-test-12345!')
        self.client.post(
            reverse('cnb_add_collaborator', args=[self.resolution.pk]),
            {'user_id': self.collaborator.pk, 'role': 'editor'},
        )
        self.collab.refresh_from_db()
        self.assertEqual(self.collab.role, 'viewer')

    def test_non_cnb_member_sees_no_role_select_in_detail_page(self):
        outsider = make_user('CNB-O2')
        self.client.logout()
        self.client.login(username=outsider.username, password='cnb-collab-test-12345!')
        response = self.client.get(reverse('cnb_resolution_detail', args=[self.resolution.pk]))
        self.assertNotContains(response, 'name="role"')
