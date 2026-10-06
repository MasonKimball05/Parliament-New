"""
Editor collaborators can actually edit a resolution (v3.41.1, 10-02-26).

Mason: "The edit resolution page required constitution and bylaws chair."
The resolution page showed the Edit button and amendment forms to
collaborators added as Editor, but `edit_resolution`, `add_amendment`,
`remove_amendment` and the section API behind the amendment modal were all
`@cnb_required`, so the role granted nothing.

Decided (Mason, 10-02-26): chair + editor collaborators can edit the text and
add/remove amendments. Status changes and collaborator management stay with
the chair.

Run with: python manage.py test src.tests.legislation.test_cnb_editor_collaborators
"""
from django.test import TestCase
from django.urls import reverse

from src.models import (
    Article, GoverningDocument, ParliamentUser, Resolution,
    ResolutionAmendment, ResolutionCollaborator, Section,
)

PASSWORD = 'cnb-editor-test-12345!'


def make_user(uid, **kwargs):
    defaults = dict(name=f'User {uid}', username=uid.lower(), member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(PASSWORD)
    user.save()
    return user


class _Base(TestCase):
    def setUp(self):
        self.chair = make_user('CNB-H1', is_admin=True)
        self.editor = make_user('CNB-E1')
        self.viewer = make_user('CNB-V1')
        self.member = make_user('CNB-M1')
        self.resolution = Resolution.objects.create(title='Original title', created_by=self.chair)
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.editor, role='editor')
        ResolutionCollaborator.objects.create(resolution=self.resolution, user=self.viewer, role='viewer')

        self.document = GoverningDocument.objects.filter(doc_type='constitution').first() \
            or GoverningDocument.objects.create(doc_type='constitution', title='Constitution')
        self.article = Article.objects.create(document=self.document, number='XCIX', title='Test', display_order=999)
        self.section = Section.objects.create(article=self.article, number='1', title='Sec',
                                              content='Old text.', display_order=1)

    def login(self, user):
        self.client.force_login(user)

    @property
    def edit_url(self):
        return reverse('cnb_edit_resolution', args=[self.resolution.pk])


class EditPageTests(_Base):

    def test_editor_can_open_and_save(self):
        self.login(self.editor)
        self.assertEqual(self.client.get(self.edit_url).status_code, 200)
        self.client.post(self.edit_url, {'title': 'Edited by editor', 'resolution_type': 'amendment'})
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.title, 'Edited by editor')

    def test_chair_still_can(self):
        self.login(self.chair)
        self.assertEqual(self.client.get(self.edit_url).status_code, 200)

    def test_viewer_and_plain_member_cannot(self):
        for user in (self.viewer, self.member):
            with self.subTest(user=user.user_id):
                self.login(user)
                self.assertEqual(self.client.get(self.edit_url).status_code, 302)
                self.client.post(self.edit_url, {'title': 'Hijacked', 'resolution_type': 'amendment'})
                self.resolution.refresh_from_db()
                self.assertEqual(self.resolution.title, 'Original title')

    def test_editor_of_another_resolution_cannot(self):
        other = Resolution.objects.create(title='Other', created_by=self.chair)
        self.login(self.editor)
        self.client.post(reverse('cnb_edit_resolution', args=[other.pk]),
                         {'title': 'Hijacked', 'resolution_type': 'amendment'})
        other.refresh_from_db()
        self.assertEqual(other.title, 'Other')

    def test_editor_cannot_edit_a_closed_resolution(self):
        Resolution.objects.filter(pk=self.resolution.pk).update(status='passed')
        self.login(self.editor)
        self.client.post(self.edit_url, {'title': 'After the vote', 'resolution_type': 'amendment'})
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.title, 'Original title')


class AmendmentTests(_Base):

    def _add(self):
        return self.client.post(reverse('cnb_add_amendment', args=[self.resolution.pk]),
                                {'section_id': self.section.pk, 'proposed_text': 'New text.'})

    def test_editor_can_add_and_remove(self):
        self.login(self.editor)
        self._add()
        amendment = ResolutionAmendment.objects.get(resolution=self.resolution)
        self.client.post(reverse('cnb_remove_amendment', args=[self.resolution.pk, amendment.pk]))
        self.assertFalse(ResolutionAmendment.objects.filter(resolution=self.resolution).exists())

    def test_viewer_and_plain_member_cannot_add(self):
        for user in (self.viewer, self.member):
            with self.subTest(user=user.user_id):
                self.login(user)
                self._add()
                self.assertFalse(ResolutionAmendment.objects.exists())

    def test_plain_member_cannot_remove(self):
        amendment = ResolutionAmendment.objects.create(
            resolution=self.resolution, section=self.section, proposed_text='x',
            original_text_snapshot='Old text.', amendment_type='change')
        self.login(self.member)
        self.client.post(reverse('cnb_remove_amendment', args=[self.resolution.pk, amendment.pk]))
        self.assertTrue(ResolutionAmendment.objects.filter(pk=amendment.pk).exists())

    def test_section_api(self):
        url = reverse('cnb_section_api', args=[self.section.pk])
        self.login(self.editor)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login(self.member)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)


class ChairOnlyTests(_Base):

    def test_editor_cannot_change_status(self):
        self.login(self.editor)
        self.client.post(reverse('cnb_set_status', args=[self.resolution.pk]), {'status': 'pending'})
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.status, 'draft')

    def test_editor_cannot_add_collaborators(self):
        self.login(self.editor)
        self.client.post(reverse('cnb_add_collaborator', args=[self.resolution.pk]),
                         {'user_id': self.member.pk, 'role': 'editor'})
        self.assertFalse(ResolutionCollaborator.objects.filter(user=self.member).exists())

    def test_detail_page_shows_editor_the_edit_link_but_not_status_actions(self):
        self.login(self.editor)
        response = self.client.get(reverse('cnb_resolution_detail', args=[self.resolution.pk]))
        self.assertContains(response, self.edit_url)
        self.assertNotContains(response, reverse('cnb_set_status', args=[self.resolution.pk]))

    def test_detail_page_shows_chair_the_status_actions(self):
        self.login(self.chair)
        response = self.client.get(reverse('cnb_resolution_detail', args=[self.resolution.pk]))
        self.assertContains(response, reverse('cnb_set_status', args=[self.resolution.pk]))
