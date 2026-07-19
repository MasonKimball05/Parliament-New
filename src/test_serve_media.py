"""
v3.14.1 — authenticated /media/ serving (src/view/serve_media.py).

Locks in the fix for the 07-18-26 finding that nginx served all uploaded
legislation docs publicly. If any of these fail, assume the auth wall on
member uploads is broken.
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from src.models import ParliamentUser

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-media-')


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ServeMediaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.makedirs(os.path.join(_TMP_MEDIA, 'legislation_docs'), exist_ok=True)
        with open(os.path.join(_TMP_MEDIA, 'legislation_docs', 'doc.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 test payload')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='sm1', name='Media Member', username='sm1',
            member_type='Member')
        self.url = reverse('serve_media',
                           kwargs={'path': 'legislation_docs/doc.pdf'})

    def test_anonymous_is_redirected_to_login(self):
        """THE regression test: /media/ must never serve without a session."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_logged_in_member_gets_file(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('private', resp['Cache-Control'])
        self.assertEqual(b''.join(resp.streaming_content),
                         b'%PDF-1.4 test payload')

    def test_traversal_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            '/media/legislation_docs/%2e%2e/%2e%2e/etc/passwd')
        self.assertIn(resp.status_code, (404, 400))

    def test_missing_file_404s(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('serve_media',
                                       kwargs={'path': 'nope/missing.pdf'}))
        self.assertEqual(resp.status_code, 404)

    def test_awkward_filename_headers(self):
        """v3.14.2: spaces/quotes/non-ASCII in an uploaded filename must
        yield a quoted X-Accel URI and an RFC 5987 Content-Disposition."""
        fname = 'Résolution "Fall" 2026.pdf'
        with open(os.path.join(_TMP_MEDIA, 'legislation_docs', fname), 'wb') as f:
            f.write(b'%PDF-1.4 awkward')
        self.client.force_login(self.member)
        url = reverse('serve_media',
                      kwargs={'path': f'legislation_docs/{fname}'})

        # FileResponse mode: header must parse (no raw quote/space breakage)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        cd = resp['Content-Disposition']
        self.assertTrue(cd.startswith('inline'))
        self.assertNotIn('"Fall"', cd)  # raw inner quotes would corrupt the header
        self.assertIn("filename*=utf-8''", cd)  # RFC 5987 form present

        # Accel mode: internal URI must be percent-quoted, no raw specials
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(url)
        accel = resp['X-Accel-Redirect']
        self.assertTrue(accel.startswith('/internal_media/legislation_docs/'))
        for raw in (' ', '"'):
            self.assertNotIn(raw, accel)
        self.assertIn('%20', accel)

    def test_accel_redirect_mode(self):
        """With MEDIA_ACCEL_PREFIX set, Django delegates the byte-shoving to
        nginx via X-Accel-Redirect but still enforces auth first."""
        self.client.force_login(self.member)
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['X-Accel-Redirect'],
                         '/internal_media/legislation_docs/doc.pdf')
        # And anonymous still bounces even in accel mode
        self.client.logout()
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
