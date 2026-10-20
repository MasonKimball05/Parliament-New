"""
v3.51.0 — any brother can write a resolution and runs his own.

Mason, 10-19-26: "Update the Resolutions page so anyone can use or make a
resolution — this should be open to everyone." His calls: brothers only
(Members, Chairs, Officers, admins; not pledges or advisors), and the author
gets full control of his own resolution EXCEPT Passed / Failed, which apply
amendments / protection to the governing documents and stay with the C&B Chair.

Run with: python manage.py test src.tests.legislation.test_resolutions_open_to_brothers
"""
from django.test import TestCase
from django.urls import reverse

from src.models import (
    ParliamentUser, Resolution, ResolutionCollaborator, ResolutionNote,
)


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member',
                    member_status='Active', email=f'{uid.lower()}@example.com')
    defaults.update(kwargs)
    return ParliamentUser.objects.create(user_id=uid, **defaults)


class CreateTests(TestCase):
    def test_brothers_can_create(self):
        for i, mt in enumerate(('Member', 'Chair', 'Officer')):
            user = make_user(f'RB-C{i}', member_type=mt)
            self.client.force_login(user)
            r = self.client.post(reverse('cnb_create_resolution'), {'title': f'By a {mt}'})
            res = Resolution.objects.get(title=f'By a {mt}')
            self.assertRedirects(r, reverse('cnb_resolution_detail', args=[res.pk]), fetch_redirect_response=False)
            self.assertEqual(res.created_by, user)
            self.assertEqual(self.client.get(reverse('cnb_create_resolution')).status_code, 200)

    def test_pledges_and_advisors_cannot_create(self):
        for i, mt in enumerate(('Pledge', 'Advisor')):
            self.client.force_login(make_user(f'RB-N{i}', member_type=mt))
            r = self.client.post(reverse('cnb_create_resolution'), {'title': f'By a {mt}'})
            self.assertEqual(r.status_code, 302)
            self.assertFalse(Resolution.objects.filter(title=f'By a {mt}').exists())

    def test_hub_shows_new_resolution_to_members_not_pledges(self):
        self.client.force_login(make_user('RB-H1'))
        self.assertContains(self.client.get(reverse('constitution_bylaws')), reverse('cnb_create_resolution'))
        self.client.force_login(make_user('RB-H2', member_type='Pledge'))
        self.assertNotContains(self.client.get(reverse('constitution_bylaws')), reverse('cnb_create_resolution'))


class _AuthorBase(TestCase):
    def setUp(self):
        self.author = make_user('RB-A1')
        self.other = make_user('RB-O1')
        self.pledge = make_user('RB-P1', member_type='Pledge')
        self.chair = make_user('RB-CH', is_admin=True)
        self.res = Resolution.objects.create(title='Mine', created_by=self.author)
        self.client.force_login(self.author)

    def status(self, value, user=None):
        if user:
            self.client.force_login(user)
        self.client.post(reverse('cnb_set_status', args=[self.res.pk]), {'status': value})
        self.res.refresh_from_db()
        return self.res.status


class AuthorControlTests(_AuthorBase):
    def test_author_edits_own_resolution(self):
        r = self.client.post(reverse('cnb_edit_resolution', args=[self.res.pk]), {'title': 'Mine, edited', 'changed_fields': 'title'})
        self.res.refresh_from_db()
        self.assertEqual(self.res.title, 'Mine, edited', r.status_code)

    def test_other_member_cannot_edit_or_manage(self):
        self.client.force_login(self.other)
        self.client.post(reverse('cnb_edit_resolution', args=[self.res.pk]), {'title': 'Hijacked', 'changed_fields': 'title'})
        self.res.refresh_from_db()
        self.assertEqual(self.res.title, 'Mine')
        self.assertEqual(self.status('pending'), 'draft')
        self.client.post(reverse('cnb_add_collaborator', args=[self.res.pk]),
                         {'user_id': self.other.pk, 'role': 'editor'})
        self.assertFalse(ResolutionCollaborator.objects.filter(resolution=self.res).exists())

    def test_author_moves_draft_pending_withdrawn(self):
        self.assertEqual(self.status('pending'), 'pending')
        self.assertEqual(self.status('draft'), 'draft')
        self.assertEqual(self.status('withdrawn'), 'withdrawn')

    def test_author_cannot_pass_or_fail(self):
        self.status('pending')
        self.assertEqual(self.status('passed'), 'pending')
        self.assertEqual(self.status('failed'), 'pending')
        self.assertIsNone(self.res.passed_at)

    def test_chair_still_records_the_result(self):
        self.status('pending')
        self.assertEqual(self.status('passed', user=self.chair), 'passed')

    def test_author_who_becomes_a_pledge_or_leaves_loses_control(self):
        ParliamentUser.objects.filter(pk=self.author.pk).update(member_type='Advisor')
        self.assertEqual(self.status('pending'), 'draft')

    def test_author_adds_and_removes_brother_collaborators(self):
        self.client.post(reverse('cnb_add_collaborator', args=[self.res.pk]),
                         {'user_id': self.other.pk, 'role': 'editor'})
        collab = ResolutionCollaborator.objects.get(resolution=self.res, user=self.other)
        self.assertEqual(collab.role, 'editor')
        self.client.post(reverse('cnb_remove_collaborator', args=[self.res.pk, collab.pk]))
        self.assertFalse(ResolutionCollaborator.objects.filter(pk=collab.pk).exists())

    def test_author_cannot_add_a_pledge(self):
        self.client.post(reverse('cnb_add_collaborator', args=[self.res.pk]),
                         {'user_id': self.pledge.pk, 'role': 'editor'})
        self.assertFalse(ResolutionCollaborator.objects.filter(resolution=self.res).exists())

    def test_chair_can_still_add_anyone(self):
        self.client.force_login(self.chair)
        self.client.post(reverse('cnb_add_collaborator', args=[self.res.pk]),
                         {'user_id': self.pledge.pk, 'role': 'viewer'})
        self.assertTrue(ResolutionCollaborator.objects.filter(resolution=self.res, user=self.pledge).exists())

    def test_detail_page_controls(self):
        Resolution.objects.filter(pk=self.res.pk).update(status='pending')
        html = self.client.get(reverse('cnb_resolution_detail', args=[self.res.pk])).content.decode()
        self.assertIn('Return to Draft', html)
        self.assertIn('id="js-open-collab-modal"', html)
        self.assertNotIn('value="passed"', html)
        self.assertNotIn('value="failed"', html)
        self.assertNotIn(self.pledge.name, html)  # not offered in the add-collaborator list
        self.client.force_login(self.chair)
        html = self.client.get(reverse('cnb_resolution_detail', args=[self.res.pk])).content.decode()
        self.assertIn('value="passed"', html)

    def test_other_member_sees_no_controls(self):
        self.client.force_login(self.other)
        html = self.client.get(reverse('cnb_resolution_detail', args=[self.res.pk])).content.decode()
        self.assertNotIn('id="js-open-collab-modal"', html)
        self.assertNotIn('cnb_set_status', html)
        self.assertNotIn(reverse('cnb_set_status', args=[self.res.pk]), html)

    def test_author_uses_and_clears_notes(self):
        self.assertTrue(ResolutionNote.user_can_use_notes(self.author, self.res))
        self.assertFalse(ResolutionNote.user_can_use_notes(self.other, self.res))
        ResolutionCollaborator.objects.create(resolution=self.res, user=self.other, role='editor')
        note = ResolutionNote.objects.create(resolution=self.res, created_by=self.other, body='x')
        self.assertTrue(note.user_can_delete(self.author))
