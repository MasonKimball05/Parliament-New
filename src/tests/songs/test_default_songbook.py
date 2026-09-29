"""
09-28-26 — multi-chapter: the default songbook is fraternity content
(fraternity_content/<fraternity>/songs.json), shared by every chapter; each
chapter adds its own songs in the app.

Pins:
  * the shipped songs.json is valid, and update_song_lyrics reads it;
  * seed_default_songs creates what's missing, never edits or deletes an
    existing song (a chapter's own songs, or a chorister's edits), skips
    placeholder lyrics, and is idempotent; --dry-run writes nothing;
  * export_songs round-trips into seed_default_songs.

Run with: python manage.py test src.tests.songs.test_default_songbook
"""
import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from src.fraternity_content import (FraternityContentError, has_lyrics, load_default_songs,
                                    validate_songbook)
from src.models import Song, SongCategory


def _run(*args, **kw):
    out = io.StringIO()
    call_command(*args, stdout=out, stderr=io.StringIO(), **kw)
    return out.getvalue()


class DefaultSongbookTests(TestCase):
    def test_shipped_songbook_is_valid(self):
        book = load_default_songs()
        self.assertGreaterEqual(len(book['songs']), 50)
        titles = {s['title'] for s in book['songs']}
        self.assertIn('The Beta Shrine', titles)

    def test_seed_creates_missing_songs_and_categories(self):
        book = load_default_songs()
        expected = sum(1 for s in book['songs'] if has_lyrics(s))
        _run('seed_default_songs')
        self.assertEqual(Song.objects.count(), expected)
        self.assertEqual(SongCategory.objects.count(), len(book['categories']))
        shrine = Song.objects.get(title='The Beta Shrine')
        self.assertEqual(shrine.category.name, 'Brotherhood Songs')

    def test_seed_is_idempotent(self):
        _run('seed_default_songs')
        n = Song.objects.count()
        _run('seed_default_songs')
        self.assertEqual(Song.objects.count(), n)

    def test_seed_never_touches_existing_or_chapter_songs(self):
        mine = Song.objects.create(title='Our Chapter Song', lyrics='la la la')
        edited = Song.objects.create(title='the beta shrine', lyrics='chorister-corrected lyrics')
        _run('seed_default_songs')
        mine.refresh_from_db()
        edited.refresh_from_db()
        self.assertEqual(mine.lyrics, 'la la la')
        self.assertEqual(edited.lyrics, 'chorister-corrected lyrics')
        self.assertEqual(Song.objects.filter(title__iexact='The Beta Shrine').count(), 1)

    def test_placeholders_are_not_seeded(self):
        placeholders = [s['title'] for s in load_default_songs()['songs'] if not has_lyrics(s)]
        self.assertTrue(placeholders)
        _run('seed_default_songs')
        self.assertFalse(Song.objects.filter(title__in=placeholders).exists())

    def test_dry_run_writes_nothing(self):
        _run('seed_default_songs', dry_run=True)
        self.assertEqual(Song.objects.count(), 0)
        self.assertEqual(SongCategory.objects.count(), 0)

    def test_export_round_trips(self):
        _run('seed_default_songs')
        Song.objects.create(title='Our Chapter Song', lyrics='la la la')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'songs.json'
            call_command('export_songs', output=str(path), stdout=io.StringIO(), stderr=io.StringIO())
            before = sorted(Song.objects.values_list('title', 'lyrics'))
            Song.objects.all().delete()
            SongCategory.objects.all().delete()
            _run('seed_default_songs', source=str(path))
            self.assertEqual(sorted(Song.objects.values_list('title', 'lyrics')), before)

    def test_update_song_lyrics_reads_the_file(self):
        Song.objects.create(title='The Beta Shrine', lyrics='[Lyrics pending]')
        _run('update_song_lyrics')
        self.assertNotIn('[Lyrics', Song.objects.get(title='The Beta Shrine').lyrics)

    def test_validation(self):
        good_song = {'title': 'A', 'lyrics': 'x'}
        for bad in ({'songs': []},
                    {'songs': [{'title': 'A'}]},
                    {'songs': [good_song, {'title': 'a', 'lyrics': 'y'}]},
                    {'songs': [{**good_song, 'category': 'Nope'}]},
                    {'songs': [{**good_song, 'oops': 1}]},
                    {'categories': [{'name': 'C', 'color': 'orange'}], 'songs': [good_song]}):
            with self.subTest(bad=bad):
                with self.assertRaises(FraternityContentError):
                    validate_songbook(bad)
