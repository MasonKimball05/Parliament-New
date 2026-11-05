"""v4.4.5: shortened addresses go to the page their folder is about, not a 404."""
from django.test import TestCase
from django.urls import reverse

from src.urls import _FOLDER_REDIRECTS


class FolderRedirectTests(TestCase):

    def test_each_folder_redirects_to_its_page(self):
        for route, target in _FOLDER_REDIRECTS:
            r = self.client.get('/' + route)
            self.assertEqual(r.status_code, 302, route)
            self.assertEqual(r['Location'], reverse(target), route)

    def test_pages_under_the_folders_are_untouched(self):
        # Only the bare folder redirects; routes under it still resolve to their own views.
        r = self.client.get(reverse('songbook'))
        self.assertNotEqual(r.status_code, 404)
