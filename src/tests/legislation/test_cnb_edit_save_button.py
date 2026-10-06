"""
The resolution edit page has a plain Save button (v3.41.2, 10-02-26).

Mason: "the edit page only has a save and preview button, can you add just an
edit button to save without previewing?" There was one, but it was labelled
"Edit Resolution", which did not read as "save", and it left the page. On the
edit page it is now "Save" and stays there.

Run with: python manage.py test src.tests.legislation.test_cnb_edit_save_button
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, Resolution


class SaveButtonTests(TestCase):
    def setUp(self):
        self.chair = ParliamentUser.objects.create(
            user_id='CNB-S1', name='Chair', username='cnbs1', member_type='Member',
            member_status='Active', is_admin=True)
        self.resolution = Resolution.objects.create(title='T', created_by=self.chair)
        self.client.force_login(self.chair)
        self.url = reverse('cnb_edit_resolution', args=[self.resolution.pk])

    def test_edit_page_has_a_save_button_and_keeps_save_and_preview(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="save_stay"')
        self.assertContains(response, 'js-save-and-preview-btn')
        self.assertNotContains(response, 'Edit Resolution\n                </button>')

    def test_save_saves_and_stays_on_the_edit_page(self):
        response = self.client.post(self.url, {'title': 'Saved', 'resolution_type': 'amendment', 'save_stay': '1'})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.resolution.refresh_from_db()
        self.assertEqual(self.resolution.title, 'Saved')

    def test_save_and_preview_still_goes_to_the_preview(self):
        response = self.client.post(self.url, {'title': 'P', 'resolution_type': 'amendment', 'save_and_preview': '1'})
        self.assertIn(reverse('cnb_resolution_print', args=[self.resolution.pk]), response['Location'])

    def test_create_page_still_says_create(self):
        response = self.client.get(reverse('cnb_create_resolution'))
        self.assertContains(response, 'Create Resolution')
        self.assertNotContains(response, 'name="save_stay"')
