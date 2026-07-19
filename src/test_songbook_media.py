"""
v3.15.0 — regression tests for the songbook file-serving views
(src/view/songbook.py: serve_exportable_media, serve_song_audio).

These two views had ZERO coverage despite being login-gated file servers —
the same class of endpoint as /media/ (whose missing auth was the 07-18 🔴).
Locks in: the auth wall, the traversal guard (including the sibling-directory
case tightened in v3.14.2), and the RFC 5987 Content-Disposition headers.
"""
import os
import shutil
import tempfile
from unittest import mock

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from src.models import ParliamentUser, Song

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-songmedia-')
_TMP_BASE = tempfile.mkdtemp(prefix='parliament-test-songbase-')


@override_settings(MEDIA_ROOT=_TMP_MEDIA, BASE_DIR=_TMP_BASE)
class ServeExportableMediaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        exportable = os.path.join(_TMP_BASE, 'exportable_media')
        os.makedirs(exportable, exist_ok=True)
        with open(os.path.join(exportable, 'seal.png'), 'wb') as f:
            f.write(b'\x89PNG fake seal')
        # Sibling directory that the pre-v3.14.2 bare-startswith guard
        # would have wrongly allowed
        sibling = os.path.join(_TMP_BASE, 'exportable_media_evil')
        os.makedirs(sibling, exist_ok=True)
        with open(os.path.join(sibling, 'secret.txt'), 'wb') as f:
            f.write(b'should never be served')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='sb1', name='Song Member', username='sb1',
            member_type='Member')
        self.url = reverse('serve_exportable_media',
                           kwargs={'filename': 'seal.png'})

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_member_gets_file_with_sane_headers(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'image/png')
        self.assertIn('private', resp['Cache-Control'])
        self.assertTrue(resp['Content-Disposition'].startswith('inline'))
        self.assertEqual(b''.join(resp.streaming_content), b'\x89PNG fake seal')

    def test_traversal_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            '/exportable_media/%2e%2e/%2e%2e/etc/passwd')
        self.assertIn(resp.status_code, (404, 400))

    def test_sibling_directory_is_blocked(self):
        """v3.14.2 guard tightening: `exportable_media_evil/` must not be
        reachable via `../exportable_media_evil/` even though its realpath
        starts with the exportable_media string prefix."""
        self.client.force_login(self.member)
        resp = self.client.get(
            '/exportable_media/%2e%2e/exportable_media_evil/secret.txt')
        self.assertIn(resp.status_code, (404, 400))

    def test_missing_file_404s(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('serve_exportable_media',
                                       kwargs={'filename': 'nope.png'}))
        self.assertEqual(resp.status_code, 404)


@override_settings(MEDIA_ROOT=_TMP_MEDIA, BASE_DIR=_TMP_BASE)
class ServeSongAudioTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        audio_dir = os.path.join(_TMP_MEDIA, 'songbook', 'audio')
        os.makedirs(audio_dir, exist_ok=True)
        with open(os.path.join(audio_dir, 'anthem.mp3'), 'wb') as f:
            f.write(b'ID3 fake mp3 bytes')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='sb2', name='Audio Member', username='sb2',
            member_type='Member')
        self.song = Song.objects.create(
            title='Test Anthem', is_active=True,
            audio_file='songbook/audio/anthem.mp3')
        self.url = reverse('song_audio', kwargs={'pk': self.song.pk})

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_member_streams_audio(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'audio/mpeg')
        self.assertTrue(resp['Content-Disposition'].startswith('inline'))
        self.assertEqual(b''.join(resp.streaming_content), b'ID3 fake mp3 bytes')

    def test_song_without_audio_404s(self):
        silent = Song.objects.create(title='Silent Song', is_active=True)
        self.client.force_login(self.member)
        resp = self.client.get(reverse('song_audio', kwargs={'pk': silent.pk}))
        self.assertEqual(resp.status_code, 404)

    def test_inactive_song_404s(self):
        self.song.is_active = False
        self.song.save()
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_traversal_in_stored_path_is_blocked(self):
        """A malicious stored audio_file path must not escape both roots."""
        self.song.audio_file = '../../etc/passwd'
        self.song.save()
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 404)
